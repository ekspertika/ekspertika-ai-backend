"""Stateless compliance pipeline: PDF bytes → JSON results.

Port of nextjs-fe/lib/ai/orchestrator.ts.run() but without Supabase coupling — that lives in
a separate flow (see Beads python-be-x7h.3). Use `checker=` to swap implementations:
BasicChecker (now), RAGChecker (Epic 2), MultiAgentOrchestrator (Epic 3).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.models.check_item import ComplianceResult
from app.services.chunker import chunk_pages
from app.services.compliance_checker import BasicChecker, Checker
from app.services.config_loader import load_all_check_items
from app.services.pdf_extractor import PDFExtractor
from config.config import Config

logger = logging.getLogger(__name__)

# Sequential one-at-a-time matches the FE orchestrator (BATCH_SIZE=1) — eliminates 429s and keeps
# per-call cost predictable. Total runtime ~10–15 min for the full check list.
_INTER_CALL_DELAY_SEC = 1.0


async def run_compliance_check(
    pdf_bytes: bytes,
    filename: str = "document.pdf",
    checker: Checker | None = None,
    on_progress: Callable[[str], Awaitable[None] | None] | None = None,
) -> dict:
    """Extract → chunk → check every item → return JSON-serialisable summary.

    `on_progress` lets callers stream status updates (e.g. SSE, WebSocket, Supabase row updates).
    """

    async def _emit(msg: str) -> None:
        logger.info(msg)
        if on_progress is None:
            return
        result = on_progress(msg)
        if asyncio.iscoroutine(result):
            await result

    extractor = PDFExtractor()
    pdf = extractor.extract_from_bytes(pdf_bytes, filename)
    await _emit(f"PDF extracted: {pdf.page_count} pages, {pdf.total_chars} chars")

    chunks = chunk_pages(pdf.pages)
    await _emit(f"Chunked into {len(chunks)} segments")

    items = load_all_check_items()
    await _emit(f"Loaded {len(items)} check items from compliance.config.json")

    if checker is None:
        if Config.USE_AGENTS:
            # Stage 3 — route each item to a specialized agent. The
            # orchestrator's own fallback respects Config.USE_RAG when
            # picking the unmapped-item checker, so USE_AGENTS overrides
            # USE_RAG at the flow level.
            from app.agents.orchestrator import AgentOrchestrator

            checker = AgentOrchestrator()
        elif Config.USE_RAG:
            # Lazy import — RAGChecker pulls in chromadb (the [rag] extra).
            from app.services.compliance_checker import RAGChecker

            checker = RAGChecker()
        else:
            checker = BasicChecker()

    results: list[ComplianceResult] = []
    for i, item in enumerate(items, start=1):
        await _emit(f"[{i}/{len(items)}] {item.code}")
        result = await checker.check(item, chunks)
        results.append(result)
        if i < len(items):
            await asyncio.sleep(_INTER_CALL_DELAY_SEC)

    return {
        "filename": pdf.filename,
        "page_count": pdf.page_count,
        "is_scanned": pdf.is_scanned,
        "warnings": pdf.warnings,
        "results": [r.model_dump() for r in results],
        "summary": {
            "total": len(results),
            "pass": sum(1 for r in results if r.status == "pass"),
            "partial": sum(1 for r in results if r.status == "partial"),
            "fail": sum(1 for r in results if r.status == "fail"),
            "errors": sum(1 for r in results if r.is_error),
        },
    }
