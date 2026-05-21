"""One-shot STR ingest script: PDF → text → chunks → embeddings → Chroma.

Beads: python-be-e6n.5 (Stage-2 RAG knowledge base setup).

What it does
------------
1. Loads ``data_pipeline/str_registry.json`` via ``str_registry_loader``.
2. For every entry whose PDF exists at ``data/str_texts/<filename>``:
   - extracts text with ``PDFExtractor``
   - chunks with ``chunk_str_pdf`` (800 words / 100 overlap — STR defaults)
   - embeds with ``Embedder`` (text-embedding-3-small, batched 100)
   - upserts into ``STRVectorStore`` (Chroma persistent client)
3. Skips entries whose PDF isn't on disk yet (those land via e6n.9).
4. Prints a per-STR breakdown + total vectors + embedding-cost estimate.

Idempotent: chunk IDs are deterministic (``<str_code>::<chunk_index>``), so
re-running just overwrites. Use ``--reset`` to nuke the collection first
(useful when chunker logic changes and chunk indices shift).

Usage
-----
    uv sync --extra rag
    uv run python scripts/ingest_str.py
    uv run python scripts/ingest_str.py --codes "STR 2.02.01:2004,STR 2.05.05:2005"
    uv run python scripts/ingest_str.py --reset
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo paths — keep self-contained so the script also works when invoked from
# a different cwd (e.g. CI or a docker exec).
REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = REPO_ROOT / "data" / "str_texts"
VECTOR_STORE_DIR = REPO_ROOT / "vector_store"

# Make ``app.*`` and ``config.*`` importable when running as a plain script.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.str_registry_entry import STRRegistryEntry  # noqa: E402
from app.services.embedder import Embedder  # noqa: E402
from app.services.pdf_extractor import PDFExtractor  # noqa: E402
from app.services.str_chunker import STRChunk, chunk_str_pdf  # noqa: E402
from app.services.str_registry_loader import load_str_registry  # noqa: E402
from app.services.vector_store import STRVectorStore  # noqa: E402

logger = logging.getLogger("ingest_str")

# text-embedding-3-small pricing as of 2026-04. Update if the model or price
# changes; the report is purely informational.
EMBEDDING_PRICE_PER_M_TOKENS_USD = 0.02
# Rough token-per-word factor for Lithuanian text. Varies, this is just for
# the cost estimate.
TOKENS_PER_WORD = 1.4


@dataclass
class IngestResult:
    code: str
    filename: str
    status: str  # "ingested" | "skipped" | "failed"
    chunk_count: int = 0
    page_count: int = 0
    word_count: int = 0
    note: str = ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest STR PDFs into the Chroma vector store.")
    p.add_argument(
        "--codes",
        type=str,
        default=None,
        help="Comma-separated STR codes to limit the run (default: every entry on disk).",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Drop the str_chunks collection before ingesting. Use after chunker changes.",
    )
    p.add_argument(
        "--persist-dir",
        type=str,
        default=str(VECTOR_STORE_DIR),
        help=f"Chroma persistent dir (default: {VECTOR_STORE_DIR}).",
    )
    p.add_argument(
        "--chunk-words",
        type=int,
        default=800,
        help="Chunk size in words (default: 800).",
    )
    p.add_argument(
        "--overlap-words",
        type=int,
        default=100,
        help="Chunk overlap in words (default: 100).",
    )
    return p.parse_args()


def _filter_targets(
    registry: list[STRRegistryEntry],
    selected_codes: set[str] | None,
) -> list[STRRegistryEntry]:
    if selected_codes is None:
        return list(registry)
    by_code = {e.code: e for e in registry}
    missing = selected_codes - by_code.keys()
    if missing:
        logger.warning("Codes not in registry, skipping: %s", sorted(missing))
    return [by_code[c] for c in selected_codes if c in by_code]


async def _ingest_one(
    entry: STRRegistryEntry,
    pdf_path: Path,
    extractor: PDFExtractor,
    embedder: Embedder,
    store: STRVectorStore,
    chunk_words: int,
    overlap_words: int,
) -> IngestResult:
    logger.info("[%s] extracting %s", entry.code, pdf_path.name)
    extracted = extractor.extract(str(pdf_path))
    if extracted.is_scanned:
        logger.warning(
            "[%s] looks scanned (low text density) — chunks may be poor quality",
            entry.code,
        )

    chunks: list[STRChunk] = chunk_str_pdf(
        str_code=entry.code,
        pages=extracted.pages,
        chunk_words=chunk_words,
        overlap_words=overlap_words,
    )
    if not chunks:
        return IngestResult(
            code=entry.code,
            filename=entry.filename,
            status="failed",
            page_count=extracted.page_count,
            note="no chunks produced (empty PDF?)",
        )

    word_count = sum(len(c.text.split()) for c in chunks)
    logger.info(
        "[%s] chunked: %d chunks across %d pages (~%d words)",
        entry.code,
        len(chunks),
        extracted.page_count,
        word_count,
    )

    logger.info("[%s] embedding %d chunks", entry.code, len(chunks))
    vectors = await embedder.embed([c.text for c in chunks])
    if len(vectors) != len(chunks):
        return IngestResult(
            code=entry.code,
            filename=entry.filename,
            status="failed",
            chunk_count=len(chunks),
            page_count=extracted.page_count,
            note=f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks",
        )

    logger.info("[%s] upserting into vector store", entry.code)
    store.upsert(chunks=chunks, embeddings=vectors, agent_routing=entry.agent_routing)

    return IngestResult(
        code=entry.code,
        filename=entry.filename,
        status="ingested",
        chunk_count=len(chunks),
        page_count=extracted.page_count,
        word_count=word_count,
        note="ok",
    )


def _print_report(results: list[IngestResult], store_counts: dict[str, int]) -> None:
    print("\n=== Ingest report ===")
    print(f"{'STR code':<22} {'status':<10} {'chunks':>7} {'pages':>6} {'words':>8}  note")
    print("-" * 80)
    total_chunks = 0
    total_words = 0
    for r in results:
        print(
            f"{r.code:<22} {r.status:<10} {r.chunk_count:>7} {r.page_count:>6} "
            f"{r.word_count:>8}  {r.note}"
        )
        if r.status == "ingested":
            total_chunks += r.chunk_count
            total_words += r.word_count
    print("-" * 80)

    n_ingested = sum(1 for r in results if r.status == "ingested")
    n_skipped = sum(1 for r in results if r.status == "skipped")
    n_failed = sum(1 for r in results if r.status == "failed")
    est_tokens = int(total_words * TOKENS_PER_WORD)
    est_cost_usd = est_tokens / 1_000_000 * EMBEDDING_PRICE_PER_M_TOKENS_USD

    print(
        f"\nIngested: {n_ingested}  skipped: {n_skipped}  failed: {n_failed}\n"
        f"Total chunks created this run: {total_chunks}\n"
        f"Estimated embedding tokens this run: ~{est_tokens:,}  "
        f"(~${est_cost_usd:.4f} at ${EMBEDDING_PRICE_PER_M_TOKENS_USD}/1M)"
    )

    print("\n=== Vector store snapshot ===")
    print(f"Total vectors: {store_counts.get('_total', 0)}")
    for code, n in sorted(store_counts.items()):
        if code == "_total":
            continue
        print(f"  {code:<22} {n:>5}")


async def _main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    selected: set[str] | None = None
    if args.codes:
        selected = {c.strip() for c in args.codes.split(",") if c.strip()}

    registry = load_str_registry()
    targets = _filter_targets(registry, selected)
    logger.info(
        "Registry has %d entries; targeting %d after --codes filter",
        len(registry),
        len(targets),
    )

    pdf_dir = PDF_DIR
    persist_dir = Path(args.persist_dir)
    logger.info("PDF dir: %s", pdf_dir)
    logger.info("Vector store dir: %s", persist_dir)

    store = STRVectorStore(persist_dir=persist_dir)
    if args.reset:
        logger.warning("--reset: dropping the str_chunks collection")
        store.reset()

    extractor = PDFExtractor()
    embedder = Embedder()

    results: list[IngestResult] = []
    for entry in targets:
        pdf_path = pdf_dir / entry.filename
        if not pdf_path.exists():
            logger.info("[%s] PDF not on disk, skipping (%s)", entry.code, pdf_path.name)
            results.append(
                IngestResult(
                    code=entry.code,
                    filename=entry.filename,
                    status="skipped",
                    note="PDF not on disk (run download_str.py)",
                )
            )
            continue
        try:
            result = await _ingest_one(
                entry=entry,
                pdf_path=pdf_path,
                extractor=extractor,
                embedder=embedder,
                store=store,
                chunk_words=args.chunk_words,
                overlap_words=args.overlap_words,
            )
        except Exception as exc:  # pragma: no cover — logged + reported
            logger.exception("[%s] ingest failed", entry.code)
            result = IngestResult(
                code=entry.code,
                filename=entry.filename,
                status="failed",
                note=f"{type(exc).__name__}: {exc}",
            )
        results.append(result)

    store_counts = store.count()
    _print_report(results, store_counts)

    n_ingested = sum(1 for r in results if r.status == "ingested")
    n_failed = sum(1 for r in results if r.status == "failed")
    if n_failed:
        return 2
    if n_ingested == 0:
        return 1
    return 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
