"""Single-item compliance checker. Port of nextjs-fe/lib/ai/compliance.ts.

The Checker Protocol is the seam where future Stage 2 (RAG) and Stage 3 (multi-agent)
implementations plug in — they replace BasicChecker without touching the orchestration flow.
"""

import json
import logging
from typing import TYPE_CHECKING, Protocol

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.models.check_item import CheckItem, ComplianceResult
from app.services.chunker import TextChunk, select_top_chunks
from app.services.rate_limiter import estimate_tokens, token_budget
from config.config import Config

if TYPE_CHECKING:
    from app.services.retriever import RetrievedChunk, STRRetriever

logger = logging.getLogger(__name__)


class Checker(Protocol):
    """Compliance check contract — one verdict per CheckItem given the project chunks."""

    async def check(self, item: CheckItem, chunks: list[TextChunk]) -> ComplianceResult: ...


def _page_label(c: TextChunk) -> str:
    if c.start_page == c.end_page:
        return f"Page {c.start_page}"
    return f"Pages {c.start_page}–{c.end_page}"


def _build_excerpts(chunks: list[TextChunk]) -> str:
    if not chunks:
        return "[No relevant excerpts found in the uploaded documents]"
    return "\n\n".join(
        f"[Excerpt {i + 1} — {_page_label(c)}]\n{c.text}" for i, c in enumerate(chunks)
    )


# IMPORTANT: source_pages semantics
# - For status "pass" or "partial": list the page numbers that contain the
#   actual evidence supporting the verdict (a subset of the excerpt pages).
# - For status "fail": source_pages MUST be an empty array []. A failed check
#   has no supporting evidence by definition; do NOT list the pages you
#   scanned. The post-processor will overwrite this anyway, but emitting the
#   right value keeps the contract honest.
_JSON_SCHEMA_TAIL = (
    '{"status":"pass"|"partial"|"fail","comment":"1-3 sentences",'
    '"confidence":0.0,"source_pages":[<page numbers from the excerpts that contain '
    "the key evidence; MUST be an empty array [] when status is \"fail\">],"
    '"citation":<null OR a concrete article reference quoted from the regulation '
    "excerpts above, formatted like 'STR 2.02.01:2004, 4.3 str.'; MUST be null when "
    "no regulation text was supplied or none of it names an article you would cite — "
    "never invent one>}"
)

_FAIL_SOURCE_PAGES_RULE = (
    'IMPORTANT: when status is "fail", source_pages MUST be []. '
    "Do not list the pages you scanned — only list pages that contain "
    'actual supporting evidence (i.e. only when status is "pass" or "partial").'
)


def _build_str_prompt(item: CheckItem, excerpts: str) -> str:
    requirement = (
        f"Known requirement:\n{item.requirement_text}"
        if item.requirement_text
        else f"Use your expert knowledge of {item.code} to assess compliance."
    )
    return (
        "You are a Lithuanian construction compliance expert.\n\n"
        "Evaluate whether the project documentation meets the following regulation.\n\n"
        f"Code: {item.code}\nTitle: {item.title}\nCategory: {item.category}\n{requirement}\n\n"
        "Relevant project document excerpts (with source page numbers):\n"
        f"{excerpts}\n\n"
        'Based on the excerpts, evaluate compliance. If the excerpts contain no relevant information, return "fail" with low confidence.\n\n'
        f"Return ONLY a JSON object:\n{_JSON_SCHEMA_TAIL}\n"
        f"{_FAIL_SOURCE_PAGES_RULE}"
    )


def _build_law_prompt(item: CheckItem, excerpts: str) -> str:
    return (
        "You are a Lithuanian construction law expert.\n\n"
        f"Check whether the project documentation references or addresses: {item.code}\n\n"
        "Project document excerpts (with source page numbers):\n"
        f"{excerpts}\n\n"
        f"Return ONLY a JSON object:\n{_JSON_SCHEMA_TAIL}\n"
        "- pass: law referenced or its requirements addressed\n"
        "- partial: indirect or partial coverage\n"
        "- fail: no reference found\n"
        f"{_FAIL_SOURCE_PAGES_RULE}"
    )


def _build_standard_prompt(item: CheckItem, excerpts: str) -> str:
    return (
        "You are a Lithuanian construction standards expert.\n\n"
        f"Check whether the project references or applies: {item.code} — {item.title}\n\n"
        "Project document excerpts (with source page numbers):\n"
        f"{excerpts}\n\n"
        f"Return ONLY a JSON object:\n{_JSON_SCHEMA_TAIL}\n"
        "- pass: standard explicitly referenced or applied\n"
        "- partial: indirect or partial application\n"
        "- fail: no reference found\n"
        f"{_FAIL_SOURCE_PAGES_RULE}"
    )


def _build_document_prompt(item: CheckItem, excerpts: str) -> str:
    return (
        "You are reviewing a Lithuanian construction project documentation set.\n\n"
        f'Check whether this required document is present or referenced: "{item.code}"\n\n'
        "Project document excerpts (with source page numbers):\n"
        f"{excerpts}\n\n"
        f"Return ONLY a JSON object:\n{_JSON_SCHEMA_TAIL}\n"
        "- pass: document explicitly present or referenced\n"
        "- partial: implied or indirectly referenced\n"
        "- fail: not found\n"
        f"{_FAIL_SOURCE_PAGES_RULE}"
    )


_PROMPT_BUILDERS = {
    "str": _build_str_prompt,
    "law": _build_law_prompt,
    "standard": _build_standard_prompt,
    "document": _build_document_prompt,
}


# --- RAG prompt augmentation -----------------------------------------------
# These helpers fold the retrieved STR regulation excerpts into the prompt
# *before* the project excerpts. The model now compares project text against
# real regulation text instead of recalling from training memory.

_REGULATION_CITE_INSTRUCTION = (
    "Cite the STR article when explaining your verdict — quote the article "
    "number you saw in the regulation excerpts."
)

# Leniency instruction added when retrieval returns chunks. Without this the
# model becomes overly literal in the presence of regulation text — it demands
# direct citation of the STR code in the project doc and otherwise returns
# 'fail'. Smoke test (e6n.10) showed a baseline 30 partial → 12 partial /
# 17 fail → 36 fail regression caused by this strictness.
_LENIENCY_INSTRUCTION = (
    "When assessing compliance, indirect references, conceptual matches, and "
    "partial coverage all count toward 'partial'. Even when the project "
    "documentation does not cite the regulation code explicitly, look for "
    "concepts, requirements, and topics that align with the regulation "
    "excerpts above. Reserve 'fail' for cases where the project documentation "
    "contains nothing relevant — not even indirect mentions of the topics "
    "this regulation governs."
)


def _build_regulation_section(retrieved: "list[RetrievedChunk]") -> str:
    """Render retrieved STR chunks as a prompt section with article + page tags.

    Returns empty string if no chunks — RAGChecker falls back to BasicChecker
    semantics in that case (see RAGChecker.check) instead of biasing the model
    with a "no excerpts available" note.
    """
    if not retrieved:
        return ""

    lines: list[str] = ["STR regulation excerpts (with article references where available):"]
    for chunk in retrieved:
        if chunk.start_page == chunk.end_page:
            page_label = f"Page {chunk.start_page}"
        else:
            page_label = f"Pages {chunk.start_page}-{chunk.end_page}"
        article_part = f", Article {chunk.article}" if chunk.article else ""
        header = f"[{chunk.str_code}{article_part}, {page_label}]"
        lines.append(f"{header}\n{chunk.text}")
    return "\n\n".join(lines)


def _augment_prompt_with_regulation(base_prompt: str, regulation_section: str) -> str:
    """Splice the regulation section + leniency + citation instructions into a
    prompt produced by one of the four base builders.

    The regulation section goes BEFORE the project excerpts so the model
    treats the regulation text as the source of truth and the project doc
    as the thing being judged. The leniency instruction explicitly counters
    the over-literal behaviour observed in the e6n.10 smoke test. The
    citation instruction lands at the end so the model knows to quote
    article numbers in its comment.
    """
    project_marker_candidates = [
        "Relevant project document excerpts (with source page numbers):\n",
        "Project document excerpts (with source page numbers):\n",
    ]
    insertion = f"{regulation_section}\n\n"
    trailing = f"\n{_LENIENCY_INSTRUCTION}\n{_REGULATION_CITE_INSTRUCTION}"
    for marker in project_marker_candidates:
        idx = base_prompt.find(marker)
        if idx != -1:
            new_prompt = base_prompt[:idx] + insertion + base_prompt[idx:]
            return new_prompt + trailing

    # Defensive fallback (shouldn't happen — all four builders include one of
    # the markers above): just prepend the regulation block.
    return f"{insertion}{base_prompt}{trailing}"


def _collect_chunk_pages(chunks: list[TextChunk]) -> list[int]:
    pages: set[int] = set()
    for c in chunks:
        for p in range(c.start_page, c.end_page + 1):
            pages.add(p)
    return sorted(pages)


def _reset_budget_before_sleep(retry_state: object) -> None:
    """tenacity ``before_sleep`` hook: clear the TPM window so the retry attempt
    starts from a fresh budget. The smoke test showed 429s clustering when the
    window was already saturated; retry-with-reset matches the FE behaviour.
    """
    token_budget.reset_window()


class BasicChecker:
    """Single LLM call per item, keyword-based chunk relevance, no retrieval.

    Stage 2 will introduce a RAGChecker that pulls actual STR text from a vector store
    before calling the LLM. Stage 3 will introduce a multi-agent orchestrator that routes
    items by `check_type` / category to specialized agents. Both replace this class
    without changing the orchestration flow — they implement the same `Checker` Protocol.
    """

    def __init__(self, model: str | None = None) -> None:
        self.client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
        self.model = model or Config.COMPLIANCE_MODEL

    async def check(self, item: CheckItem, chunks: list[TextChunk]) -> ComplianceResult:
        relevant = select_top_chunks(chunks, item.keywords, top_n=3)
        excerpts = _build_excerpts(relevant)
        chunk_pages = _collect_chunk_pages(relevant)
        prompt = _PROMPT_BUILDERS[item.check_type](item, excerpts)

        try:
            return await self._call_llm(item, prompt, chunk_pages)
        except Exception as exc:
            logger.error("Technical failure for %s: %s", item.code, exc)
            return ComplianceResult(
                str_code=item.code,
                check_type=item.check_type,
                status="fail",
                comment="Technical error — API call failed after all retries.",
                confidence=0,
                is_error=True,
                source_pages=[],
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=_reset_budget_before_sleep,
    )
    async def _call_llm(
        self, item: CheckItem, prompt: str, chunk_pages: list[int]
    ) -> ComplianceResult:
        return await _call_compliance_llm(
            client=self.client,
            model=self.model,
            item=item,
            prompt=prompt,
            chunk_pages=chunk_pages,
        )


async def _call_compliance_llm(
    *,
    client: AsyncOpenAI,
    model: str,
    item: CheckItem,
    prompt: str,
    chunk_pages: list[int],
    confidence_ceiling: float | None = None,
) -> ComplianceResult:
    """Shared LLM call + result post-processing used by BasicChecker and RAGChecker.

    ``confidence_ceiling`` lets callers cap confidence (e.g. RAGChecker uses
    this when retrieval returned 0 chunks — the model is reasoning from
    training memory only, so we shouldn't trust a high confidence value).
    """
    # Proactive TPM throttle — blocks if the 60s window would exceed 70%
    # of the configured limit. Pairs with the tenacity before_sleep hook
    # which resets the window between retry attempts.
    await token_budget.acquire(estimate_tokens(prompt))

    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=256,
    )
    raw = response.choices[0].message.content or "{}"
    parsed = json.loads(raw)

    status = parsed.get("status")
    if status not in ("pass", "partial", "fail"):
        status = "fail"

    ai_pages = [
        p for p in (parsed.get("source_pages") or [])
        if isinstance(p, int) and not isinstance(p, bool) and p > 0
    ]
    resolved_pages = ai_pages or chunk_pages

    # Belt-and-braces: failed checks have no evidence by definition. The
    # prompt schema says source_pages MUST be [] for status=fail, but
    # gpt-4o-mini sometimes treats the field as "pages I scanned" — see
    # python-be-x7h.9. Force it here regardless of model output.
    if status == "fail":
        resolved_pages = []

    confidence = float(parsed.get("confidence", 0.5))
    if confidence_ceiling is not None and confidence > confidence_ceiling:
        confidence = confidence_ceiling

    raw_citation = parsed.get("citation")
    citation = raw_citation.strip() if isinstance(raw_citation, str) and raw_citation.strip() else None
    # A "fail" verdict has no supporting evidence by construction, so it can't carry a citation —
    # mirrors the source_pages=[] rule a few lines up.
    if status == "fail":
        citation = None

    return ComplianceResult(
        str_code=item.code,
        check_type=item.check_type,
        status=status,
        comment=parsed.get("comment", ""),
        confidence=confidence,
        is_error=False,
        source_pages=resolved_pages,
        citation=citation,
    )


# --- RAG-aware checker -----------------------------------------------------


def _build_retrieval_query(item: CheckItem) -> str:
    """Cheap query construction for v1: title + keywords. Stage 3 will refine."""
    keywords = " ".join(item.keywords) if item.keywords else ""
    return f"{item.title} {keywords}".strip()


class RAGChecker:
    """Compliance checker that retrieves real STR text before calling the LLM.

    Same ``Checker`` Protocol as ``BasicChecker``; pluggable via the flow's
    ``checker=`` argument or the ``Config.USE_RAG`` env switch.

    Behaviour delta vs BasicChecker:

    * Before the LLM call, fetch top-K STR chunks from the vector store.
      ``check_type='str'`` → filter by ``item.code``;
      ``law`` / ``standard`` / ``document`` → open semantic search.
    * The retrieved chunks are spliced into the prompt as a "STR regulation
      excerpts" section *before* the project excerpts. The model now compares
      project text against actual regulation text instead of recalling from
      training memory. A leniency instruction is added so the model still
      counts indirect/conceptual matches toward 'partial' rather than 'fail'.
    * If retrieval returns 0 chunks (e.g. the relevant STR isn't ingested
      yet — only ~7 of 30 are loaded), RAGChecker falls back to the exact
      BasicChecker prompt and behaviour. No "no excerpts available" note,
      no confidence cap. Empty-retrieval items behave identically to
      BasicChecker so the regression we saw in e6n.10 disappears.
    """

    def __init__(
        self,
        retriever: "STRRetriever | None" = None,
        model: str | None = None,
        top_k: int | None = None,
    ) -> None:
        self.client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
        self.model = model or Config.COMPLIANCE_MODEL
        self.top_k = top_k or Config.RAG_TOP_K
        # Lazy import — STRRetriever pulls in vector_store which lazy-imports
        # chromadb. Keeps the module importable without the [rag] extra.
        if retriever is None:
            from app.services.retriever import STRRetriever

            retriever = STRRetriever(top_k=self.top_k)
        self.retriever = retriever

    async def check(self, item: CheckItem, chunks: list[TextChunk]) -> ComplianceResult:
        # 1. Project chunks: same keyword-based shortlist as BasicChecker so
        #    source_pages semantics stay identical. The RAG addition is
        #    *regulation* text, not project text.
        relevant = select_top_chunks(chunks, item.keywords, top_n=3)
        excerpts = _build_excerpts(relevant)
        chunk_pages = _collect_chunk_pages(relevant)

        # 2. Retrieve STR regulation excerpts.
        retrieved = await self._retrieve_for_item(item)
        base_prompt = _PROMPT_BUILDERS[item.check_type](item, excerpts)

        # 3. With retrieval: augment the base prompt with regulation text +
        #    leniency + citation instructions. Without retrieval: fall back
        #    to the exact BasicChecker prompt — same behaviour, same
        #    confidence range. (Earlier versions added a "no excerpts
        #    available" note + 0.4 confidence cap, which biased the model
        #    toward 'fail' for the 23 STRs not yet ingested. See e6n.10.)
        if retrieved:
            regulation_section = _build_regulation_section(retrieved)
            prompt = _augment_prompt_with_regulation(base_prompt, regulation_section)
        else:
            prompt = base_prompt

        try:
            return await self._call_llm(item, prompt, chunk_pages, None)
        except Exception as exc:
            logger.error("RAG technical failure for %s: %s", item.code, exc)
            return ComplianceResult(
                str_code=item.code,
                check_type=item.check_type,
                status="fail",
                comment="Technical error — API call failed after all retries.",
                confidence=0,
                is_error=True,
                source_pages=[],
            )

    async def _retrieve_for_item(
        self, item: CheckItem
    ) -> "list[RetrievedChunk]":
        query = _build_retrieval_query(item)
        try:
            if item.check_type == "str":
                return await self.retriever.retrieve_for_str(
                    item.code, query, top_k=self.top_k
                )
            return await self.retriever.retrieve_open(query, top_k=self.top_k)
        except Exception as exc:
            # Retrieval failures shouldn't kill the whole check — log and
            # fall back to "no excerpts available" / low confidence.
            logger.warning(
                "Retrieval failed for %s (%s) — falling back to no-excerpts mode: %s",
                item.code,
                item.check_type,
                exc,
            )
            return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=_reset_budget_before_sleep,
    )
    async def _call_llm(
        self,
        item: CheckItem,
        prompt: str,
        chunk_pages: list[int],
        confidence_ceiling: float | None,
    ) -> ComplianceResult:
        return await _call_compliance_llm(
            client=self.client,
            model=self.model,
            item=item,
            prompt=prompt,
            chunk_pages=chunk_pages,
            confidence_ceiling=confidence_ceiling,
        )
