"""Supabase-coupled compliance pipeline.

Mirrors `nextjs-fe/lib/ai/orchestrator.ts.run()` in Python:
1. Mark project status='processing'
2. Download every project_files row from the 'project-files' storage bucket
3. Extract text via PyMuPDF, persist back into project_files.extracted_text
4. Chunk the union of all pages
5. For every CheckItem run the Checker and INSERT one str_results row immediately
   (per-row writes give the FE natural progress streaming via polling)
6. Mark status='done', or 'failed' with the exception message

The stateless flow at app/flows/compliance_flow.py is intentionally untouched so curl/CLI
testing of the pipeline still works without a Supabase round-trip.
"""

import asyncio
import logging
from dataclasses import dataclass

from app.integrations.supabase_client import get_supabase
from app.models.check_item import ComplianceResult
from app.services.chunker import TextChunk, chunk_pages
from app.services.compliance_checker import BasicChecker, Checker
from app.services.config_loader import load_all_check_items
from app.services.pdf_extractor import PDFExtractor
from config.config import Config

logger = logging.getLogger(__name__)

_STORAGE_BUCKET = "project-files"
# Match the stateless flow: 1s breath between each LLM call.
_INTER_CALL_DELAY_SEC = 1.0


@dataclass(frozen=True)
class _FileSpan:
    """One file's slice of the global page index. Used to invert the
    chunker's global-page numbering back to per-file (file_id, file_name, page) for
    the multi-file source_files column (python-be-x7h.8)."""

    file_id: str
    file_name: str
    start_global_page: int  # inclusive, 1-based
    page_count: int

    @property
    def end_global_page(self) -> int:  # inclusive
        return self.start_global_page + self.page_count - 1

    def per_file_page(self, global_page: int) -> int:
        """Translate global page → 1-based per-file page. Caller has already
        verified ``contains(global_page)``."""
        return global_page - self.start_global_page + 1

    def contains(self, global_page: int) -> bool:
        return self.start_global_page <= global_page <= self.end_global_page


def _build_source_files(
    global_pages: list[int], spans: list[_FileSpan]
) -> list[dict] | None:
    """Decompose a list of global pages into a per-file structure for the
    ``source_files`` jsonb column.

    Returns ``None`` for empty input — matches the source_pages convention so the
    FE knows ``no evidence`` (e.g. status=fail) without a parallel-empty-arrays gotcha.
    """
    if not global_pages or not spans:
        return None

    # Preserve span order (== file upload order) and ascending pages within each span.
    by_span: dict[str, dict] = {}
    for page in global_pages:
        for span in spans:
            if span.contains(page):
                bucket = by_span.setdefault(
                    span.file_id,
                    {"file_id": span.file_id, "file_name": span.file_name, "pages": []},
                )
                bucket["pages"].append(span.per_file_page(page))
                break

    out: list[dict] = []
    for span in spans:
        bucket = by_span.get(span.file_id)
        if bucket:
            bucket["pages"] = sorted(set(bucket["pages"]))
            out.append(bucket)
    return out or None


def _result_to_row(
    project_id: str, result: ComplianceResult, spans: list[_FileSpan]
) -> dict:
    """Map ComplianceResult → str_results row shape."""
    return {
        "project_id": project_id,
        "str_code": result.str_code,
        "check_type": result.check_type,
        "status": result.status,
        "comment": result.comment,
        "confidence": result.confidence,
        "is_error": result.is_error,
        "source_pages": result.source_pages if result.source_pages else None,
        "source_files": _build_source_files(result.source_pages, spans),
    }


async def run_project_check(project_id: str, checker: Checker | None = None) -> None:
    """End-to-end project run: storage → extract → chunk → check → str_results.

    Errors are caught here and persisted to projects.error so they don't die silently
    in a BackgroundTasks context. The exception is also re-raised after persistence so
    callers / tests can observe it if they `await` this directly.
    """

    supabase = await get_supabase()

    try:
        await (
            supabase.table("projects")
            .update({"status": "processing", "error": None})
            .eq("id", project_id)
            .execute()
        )

        files_resp = await (
            supabase.table("project_files").select("*").eq("project_id", project_id).execute()
        )
        files = files_resp.data or []
        if not files:
            raise RuntimeError(f"No project_files rows found for project {project_id}")

        extractor = PDFExtractor()
        all_pages: dict[int, str] = {}
        page_offset = 0  # Re-key per-file 1-based pages to a single global index.
        spans: list[_FileSpan] = []

        for file in files:
            storage_path = file["storage_path"]
            file_id = file["id"]
            file_name = file.get("file_name") or storage_path

            logger.info("Downloading %s (project=%s)", file_name, project_id)
            file_bytes = await supabase.storage.from_(_STORAGE_BUCKET).download(storage_path)
            if not file_bytes:
                raise RuntimeError(f"Empty download for {file_name} ({storage_path})")

            extracted = extractor.extract_from_bytes(file_bytes, filename=file_name)
            await (
                supabase.table("project_files")
                .update({"extracted_text": extracted.full_text})
                .eq("id", file_id)
                .execute()
            )

            for page_num, text in extracted.pages.items():
                all_pages[page_offset + page_num] = text
            spans.append(
                _FileSpan(
                    file_id=file_id,
                    file_name=file_name,
                    start_global_page=page_offset + 1,
                    page_count=extracted.page_count,
                )
            )
            page_offset += extracted.page_count

        chunks: list[TextChunk] = chunk_pages(all_pages)
        logger.info("Project %s: %d pages, %d chunks", project_id, len(all_pages), len(chunks))

        items = load_all_check_items()
        logger.info("Loaded %d check items", len(items))

        if checker is None:
            if Config.USE_AGENTS:
                # Stage 3 — route each item to a specialized agent.
                from app.agents.orchestrator import AgentOrchestrator

                checker = AgentOrchestrator()
            elif Config.USE_RAG:
                # Lazy import — RAGChecker pulls in chromadb (the [rag] extra).
                from app.services.compliance_checker import RAGChecker

                checker = RAGChecker()
            else:
                checker = BasicChecker()

        for i, item in enumerate(items, start=1):
            logger.info("[%d/%d] %s", i, len(items), item.code)
            result = await checker.check(item, chunks)
            row = _result_to_row(project_id, result, spans)
            await supabase.table("str_results").insert(row).execute()
            if i < len(items):
                await asyncio.sleep(_INTER_CALL_DELAY_SEC)

        await (
            supabase.table("projects")
            .update({"status": "done"})
            .eq("id", project_id)
            .execute()
        )
        logger.info("Project %s complete", project_id)

    except Exception as exc:
        logger.exception("Project %s failed", project_id)
        try:
            await (
                supabase.table("projects")
                .update({"status": "failed", "error": str(exc)})
                .eq("id", project_id)
                .execute()
            )
        except Exception:
            logger.exception("Failed to persist failure status for project %s", project_id)
        raise


async def retry_errored_checks(project_id: str, checker: Checker | None = None) -> None:
    """Re-run only str_results rows where is_error=true.

    Mirrors `retryErrors()` in nextjs-fe/lib/ai/orchestrator.ts. Reuses the already-extracted
    text from project_files.extracted_text instead of re-downloading PDFs.
    """

    supabase = await get_supabase()

    try:
        await (
            supabase.table("projects")
            .update({"status": "processing", "error": None})
            .eq("id", project_id)
            .execute()
        )

        files_resp = await (
            supabase.table("project_files")
            .select("id, file_name, extracted_text")
            .eq("project_id", project_id)
            .execute()
        )

        # Reconstruct a synthetic page map: each file's full extracted text becomes a single
        # "page" — this is good enough for chunking + keyword scoring during retries since the
        # original page numbers were already persisted on the first run.
        # The retry path collapses each file to one synthetic "page", so source_files written
        # here will only carry pages=[1] per file. That's the honest output for a retry — we
        # don't have per-page granularity anymore, only per-file. (See x7h.8 for context.)
        all_pages: dict[int, str] = {}
        spans: list[_FileSpan] = []
        for idx, file in enumerate(files_resp.data or [], start=1):
            text = file.get("extracted_text")
            if not text:
                continue
            all_pages[idx] = text
            spans.append(
                _FileSpan(
                    file_id=file["id"],
                    file_name=file.get("file_name") or file["id"],
                    start_global_page=idx,
                    page_count=1,
                )
            )

        if not all_pages:
            raise RuntimeError(f"No extracted_text available for project {project_id}")

        chunks = chunk_pages(all_pages)

        errors_resp = await (
            supabase.table("str_results")
            .select("id, str_code, check_type")
            .eq("project_id", project_id)
            .eq("is_error", True)
            .execute()
        )
        error_rows = errors_resp.data or []

        if not error_rows:
            await (
                supabase.table("projects")
                .update({"status": "done"})
                .eq("id", project_id)
                .execute()
            )
            return

        items = load_all_check_items()
        items_by_code = {it.code: it for it in items}

        if checker is None:
            if Config.USE_AGENTS:
                # Stage 3 — route each item to a specialized agent.
                from app.agents.orchestrator import AgentOrchestrator

                checker = AgentOrchestrator()
            elif Config.USE_RAG:
                # Lazy import — RAGChecker pulls in chromadb (the [rag] extra).
                from app.services.compliance_checker import RAGChecker

                checker = RAGChecker()
            else:
                checker = BasicChecker()

        for i, row in enumerate(error_rows, start=1):
            item = items_by_code.get(row["str_code"])
            if item is None:
                continue
            logger.info("[retry %d/%d] %s", i, len(error_rows), item.code)
            result = await checker.check(item, chunks)
            await (
                supabase.table("str_results")
                .update(
                    {
                        "status": result.status,
                        "comment": result.comment,
                        "confidence": result.confidence,
                        "is_error": result.is_error,
                        "source_pages": result.source_pages if result.source_pages else None,
                        "source_files": _build_source_files(result.source_pages, spans),
                    }
                )
                .eq("id", row["id"])
                .execute()
            )
            if i < len(error_rows):
                await asyncio.sleep(_INTER_CALL_DELAY_SEC)

        await (
            supabase.table("projects")
            .update({"status": "done"})
            .eq("id", project_id)
            .execute()
        )

    except Exception as exc:
        logger.exception("Project %s retry failed", project_id)
        try:
            await (
                supabase.table("projects")
                .update({"status": "failed", "error": str(exc)})
                .eq("id", project_id)
                .execute()
            )
        except Exception:
            logger.exception("Failed to persist failure status for project %s", project_id)
        raise
