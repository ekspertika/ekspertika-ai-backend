import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime
from itertools import islice
from typing import Literal

import tiktoken
from openai import AsyncOpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from app.models.check_result import ComplianceReport, DocumentCheckResult, STRCheckResult
from app.models.str_registry import (
    MANDATORY_DOCUMENTS,
    STR_NORMATIVES,
    MandatoryDocument,
    NormativeEntry,
    get_by_code,
)
from app.services.pdf_extractor import ExtractedPDF
from config.config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI response schemas (structured output)
# ---------------------------------------------------------------------------


class STRCheckItem(BaseModel):
    str_code: str
    present_in_doc: bool
    status: Literal["pass", "fail", "partial", "not_applicable"]
    comment: str
    page_references: list[int]
    confidence: Literal["high", "medium", "low"]
    citation: str | None = None
    """Concrete article reference (e.g. 'STR 2.02.01:2004 4.3 str.') when the model can pin one
    down from the document. ``None`` is the honest default — the batched legacy checker has no
    STR text in context, so the LLM can only cite an article when the project doc itself names
    one (rare). RAGChecker covers the common case; the field exists here for prompt symmetry."""


class STRBatchResponse(BaseModel):
    results: list[STRCheckItem]


class DocumentCheckItem(BaseModel):
    document_name: str
    found: bool
    comment: str


class MandatoryDocsResponse(BaseModel):
    results: list[DocumentCheckItem]


class ExtractedNormatives(BaseModel):
    str_codes: list[str]
    laws: list[str]
    hn_norms: list[str]
    other_normatives: list[str]


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_COMPLIANCE_SYSTEM_PROMPT = """Jūs esate Lietuvos statybos dokumentų atitikties ekspertas, \
turintis gilių žinių apie STR reglamentus, HN normas ir kitus statybos standartus.

Jūsų užduotis – išanalizuoti statybos projekto dokumentą ir įvertinti jo atitiktį \
nurodytiems normatyviniams dokumentams.

Vertinimo kriterijai:
- "pass" – dokumentas tinkamai nurodo ir atitinka normatyvo reikalavimus
- "fail" – normatyvas nurodytas, bet reikalavimai neįvykdyti arba trūksta svarbių elementų
- "partial" – dokumentas iš dalies atitinka reikalavimus, kai kurių elementų trūksta
- "not_applicable" – šis normatyvas netaikomas šiam projektui arba jo tipui

Normatyvui esant dokumento nuorodų sąraše, tai yra teigiamas ženklas. \
Tačiau taip pat įvertinkite ar dokumento turinys atspindi normatyvo reikalavimus.
Komentarus rašykite lietuvių kalba. Būkite konkretūs ir lakoniški (1–3 sakiniai)."""

_DOCS_SYSTEM_PROMPT = """Jūs esate statybos projekto dokumentacijos ekspertas.
Patikrinkite, ar statybos projekto dokumentų pakete yra visi privalomieji dokumentai.
Komentarus rašykite lietuvių kalba."""

_EXTRACTION_SYSTEM_PROMPT = """Jūs esate statybos dokumentų analizės ekspertas.
Ištraukite visus normatyvinius dokumentus, kurie minimi pateiktame statybos projekto tekste.
Įtraukite STR kodus, LR įstatymus, HN normas, LST standartus, ISO standartus ir kt."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _batched(iterable: list, n: int):
    """Split list into chunks of size n."""
    it = iter(iterable)
    while chunk := list(islice(it, n)):
        yield chunk


def _count_tokens(text: str) -> int:
    enc = tiktoken.encoding_for_model("gpt-4o")
    return len(enc.encode(text))


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    enc = tiktoken.encoding_for_model("gpt-4o")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


def _find_relevant_pages(
    pdf: ExtractedPDF,
    normatives: list[NormativeEntry],
    include_first_pages: int = 3,
) -> dict[int, str]:
    """Collect pages that mention any of the given normative codes."""
    relevant: dict[int, str] = {}

    for norm in normatives:
        pages = pdf.get_pages_mentioning(norm.code)
        relevant.update(pages)

    # Always include the first N pages (overview / normative reference list)
    for pg in range(1, include_first_pages + 1):
        if pg in pdf.pages:
            relevant[pg] = pdf.pages[pg]

    return relevant


def _build_doc_context(pages: dict[int, str], max_tokens: int) -> str:
    """Assemble page texts into a context string, truncated to max_tokens."""
    parts = [f"[Puslapis {num}]\n{text}" for num, text in sorted(pages.items())]
    context = "\n\n".join(parts)
    return _truncate_to_tokens(context, max_tokens)


# ---------------------------------------------------------------------------
# STR Checker
# ---------------------------------------------------------------------------


class STRChecker:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

    async def check(
        self,
        pdf: ExtractedPDF,
        on_progress: Callable[[str], None] | None = None,
    ) -> ComplianceReport:
        """Run full compliance check on an extracted PDF document."""
        warnings = list(pdf.warnings)

        def progress(msg: str) -> None:
            logger.info(msg)
            if on_progress:
                on_progress(msg)

        # Step 1 — find which normatives are explicitly mentioned in the document
        progress("Ieškoma normatyvinių nuorodų dokumente...")
        mentioned_codes = await self._extract_mentioned_codes(pdf)
        logger.info("Found %d explicitly mentioned normatives via regex", len(mentioned_codes))

        # Step 2 — build the full check list (entire registry)
        all_normatives = list(STR_NORMATIVES)
        batches = list(_batched(all_normatives, Config.STR_BATCH_SIZE))
        total_batches = len(batches)

        # Step 3 — compliance check in batches
        str_results: list[STRCheckResult] = []
        for i, batch in enumerate(batches):
            progress(
                f"Tikrinama atitiktis: {i * Config.STR_BATCH_SIZE + 1}–"
                f"{min((i + 1) * Config.STR_BATCH_SIZE, len(all_normatives))} "
                f"iš {len(all_normatives)} normatyvų..."
            )
            batch_results = await self._check_batch(pdf, batch, mentioned_codes)
            str_results.extend(batch_results)

        progress(f"Patikrinta {len(str_results)} normatyvų. Tikrinami privalomieji dokumentai...")

        # Step 4 — mandatory documents check
        doc_results = await self._check_mandatory_docs(pdf)

        progress("Tikrinimas baigtas!")

        return ComplianceReport(
            filename=pdf.filename,
            checked_at=datetime.now(),
            total_pages=pdf.page_count,
            str_results=str_results,
            document_results=doc_results,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Step 1: Extract mentioned normative codes
    # ------------------------------------------------------------------

    async def _extract_mentioned_codes(self, pdf: ExtractedPDF) -> set[str]:
        """Use regex to find all normative codes explicitly cited in the document."""
        full_text = pdf.full_text
        found: set[str] = set()

        # STR pattern: STR 1.01.02:2016 / STR 2.01.01(2):1999
        str_pattern = re.compile(
            r"STR\s*\d+\.\d+[\.\d]*(?:\(\d+\))?:\d{4}", re.IGNORECASE
        )
        for m in str_pattern.finditer(full_text):
            # Normalise whitespace
            code = re.sub(r"\s+", " ", m.group()).strip().upper()
            found.add(code)

        # HN pattern: HN 42:2009
        hn_pattern = re.compile(r"HN\s*\d+:\d{4}", re.IGNORECASE)
        for m in hn_pattern.finditer(full_text):
            found.add(re.sub(r"\s+", " ", m.group()).strip().upper())

        # LST pattern
        lst_pattern = re.compile(r"LST\s+\S+", re.IGNORECASE)
        for m in lst_pattern.finditer(full_text):
            found.add(re.sub(r"\s+", " ", m.group()).strip().upper())

        # Law keywords
        law_keywords = [
            "Statybos įstatymas",
            "Aplinkos apsaugos įstatymas",
            "Saugos ir sveikatos darbe įstatymas",
            "Žemės įstatymas",
            "Teritorijų planavimo įstatymas",
            "Atliekų tvarkymo įstatymas",
            "Architektūros įstatymas",
            "Specialiųjų žemės naudojimo sąlygų įstatymas",
        ]
        for keyword in law_keywords:
            if re.search(re.escape(keyword), full_text, re.IGNORECASE):
                found.add(keyword)

        return found

    # ------------------------------------------------------------------
    # Step 3: Batch compliance check
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _check_batch(
        self,
        pdf: ExtractedPDF,
        normatives: list[NormativeEntry],
        mentioned_codes: set[str],
    ) -> list[STRCheckResult]:
        relevant_pages = _find_relevant_pages(pdf, normatives)
        doc_context = _build_doc_context(relevant_pages, Config.MAX_TOKENS_PER_CHUNK)

        normatives_list = "\n".join(
            f"{i + 1}. {n.code} — {n.full_name}" for i, n in enumerate(normatives)
        )

        user_prompt = (
            f"Patikrinkite statybos projekto dokumentą dėl atitikties šiems normatyvams:\n\n"
            f"{normatives_list}\n\n"
            f"Dokumento ištraukos (puslapiai su nuorodomis į šiuos normatyvus ir pirmieji puslapiai):\n\n"
            f"{doc_context}\n\n"
            f"Normatyvai, kurie aptikti dokumente teksto analizės metu: "
            f"{', '.join(sorted(mentioned_codes)) or '(nerasta)'}\n\n"
            f"Kiekvienam normatyvui nurodykite atitikties būklę ir komentarą lietuvių kalba."
        )

        try:
            response = await self.client.beta.chat.completions.parse(
                model=Config.COMPLIANCE_MODEL,
                messages=[
                    {"role": "system", "content": _COMPLIANCE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=STRBatchResponse,
                temperature=0.1,
            )
            parsed = response.choices[0].message.parsed
        except Exception as exc:
            logger.error("Batch check failed: %s", exc)
            return self._error_results(normatives, str(exc))

        if not parsed:
            logger.error("Empty parsed response for batch")
            return self._error_results(normatives, "Empty response")

        # Map parsed items back to our result format
        results: list[STRCheckResult] = []
        parsed_by_code = {item.str_code: item for item in parsed.results}

        for norm in normatives:
            item = parsed_by_code.get(norm.code)
            if item:
                results.append(
                    STRCheckResult(
                        code=item.str_code,
                        full_name=norm.full_name,
                        category=norm.category,
                        present_in_doc=item.present_in_doc,
                        status=item.status,
                        comment=item.comment,
                        page_references=item.page_references,
                        confidence=item.confidence,
                        citation=item.citation,
                    )
                )
            else:
                # LLM didn't return this code — mark as not checked
                results.append(
                    STRCheckResult(
                        code=norm.code,
                        full_name=norm.full_name,
                        category=norm.category,
                        present_in_doc=norm.code in mentioned_codes,
                        status="not_applicable",
                        comment="Normatyvas nebuvo įvertintas šiame tikrinimo cikle.",
                        confidence="low",
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Step 4: Mandatory documents check
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _check_mandatory_docs(self, pdf: ExtractedPDF) -> list[DocumentCheckResult]:
        # Use first 10 pages + table of contents area (most likely to list documents)
        context_pages = {num: text for num, text in pdf.pages.items() if num <= 10}
        doc_context = _build_doc_context(context_pages, max_tokens=8000)

        docs_list = "\n".join(
            f"{i + 1}. {doc.name} — {doc.description}"
            for i, doc in enumerate(MANDATORY_DOCUMENTS)
        )

        user_prompt = (
            f"Patikrinkite, ar statybos projekto dokumentų pakete yra šie privalomieji dokumentai:\n\n"
            f"{docs_list}\n\n"
            f"Dokumento pradžios ištraukos (dažniausiai čia pateikiamas dokumentų sąrašas):\n\n"
            f"{doc_context}\n\n"
            f"Kiekvienam privalomajam dokumentui nurodykite, ar jis yra, ir komentarą lietuvių kalba."
        )

        try:
            response = await self.client.beta.chat.completions.parse(
                model=Config.COMPLIANCE_MODEL,
                messages=[
                    {"role": "system", "content": _DOCS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=MandatoryDocsResponse,
                temperature=0.1,
            )
            parsed = response.choices[0].message.parsed
        except Exception as exc:
            logger.error("Mandatory docs check failed: %s", exc)
            return [
                DocumentCheckResult(
                    document_name=doc.name,
                    found=False,
                    comment=f"Tikrinimas nepavyko: {exc}",
                )
                for doc in MANDATORY_DOCUMENTS
            ]

        if not parsed:
            return [
                DocumentCheckResult(
                    document_name=doc.name,
                    found=False,
                    comment="Tikrinimas nepavyko: tuščias atsakymas.",
                )
                for doc in MANDATORY_DOCUMENTS
            ]

        parsed_by_name = {item.document_name: item for item in parsed.results}
        results: list[DocumentCheckResult] = []

        for doc in MANDATORY_DOCUMENTS:
            item = parsed_by_name.get(doc.name)
            if item:
                results.append(
                    DocumentCheckResult(
                        document_name=item.document_name,
                        found=item.found,
                        comment=item.comment,
                    )
                )
            else:
                results.append(
                    DocumentCheckResult(
                        document_name=doc.name,
                        found=False,
                        comment="Dokumentas nebuvo patikrintas.",
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _error_results(
        self, normatives: list[NormativeEntry], error: str
    ) -> list[STRCheckResult]:
        return [
            STRCheckResult(
                code=n.code,
                full_name=n.full_name,
                category=n.category,
                present_in_doc=False,
                status="error",
                comment=f"Tikrinimas nepavyko dėl techninės klaidos.",
                confidence="low",
                error_message=error,
            )
            for n in normatives
        ]
