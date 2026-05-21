"""End-to-end prod smoke test on multi-file project (python-be-x7h.8 verification).

Creates a project in Supabase, uploads N PDFs to storage with project_files rows,
fires POST /api/v1/check/{project_id} on the deployed Railway service, and polls
str_results until done — printing source_files distribution as evidence that the
new per-file column gets populated correctly.

Usage:
  uv run python scripts/smoke_test_prod_multifile.py <pdf1> [pdf2 ...] [--api-url URL]

Defaults to both testiniai PDFs and the Railway prod URL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from pathlib import Path

import httpx

from app.integrations.supabase_client import get_supabase
from config.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("smoke-prod")

DEFAULT_PDFS = [
    Path("/Users/foksler/Projects/my/roadto1m/ekspertika/nextjs-fe/testiniai-files/2. SP Zalioji 7 2025-10-30_ZIP.pdf"),
    Path("/Users/foksler/Projects/my/roadto1m/ekspertika/nextjs-fe/testiniai-files/3. SA Zalioji 7 2025-10-30_ZIP.pdf"),
]
DEFAULT_API = "https://ekspertika-be-production.up.railway.app"


async def setup_project(pdf_paths: list[Path]) -> tuple[str, list[dict]]:
    sb = await get_supabase()
    name = f"PROD SMOKE multi-file {time.strftime('%Y-%m-%d %H:%M:%S')}"
    proj = await sb.table("projects").insert({"name": name, "status": "pending"}).execute()
    project_id: str = proj.data[0]["id"]
    logger.info("Created project %s (%s)", project_id, name)

    files: list[dict] = []
    for p in pdf_paths:
        path = f"smoke/{project_id}/{uuid.uuid4()}.pdf"
        await sb.storage.from_("project-files").upload(
            path=path,
            file=p.read_bytes(),
            file_options={"content-type": "application/pdf"},
        )
        row = await sb.table("project_files").insert(
            {"project_id": project_id, "file_name": p.name, "storage_path": path}
        ).execute()
        files.append({"id": row.data[0]["id"], "file_name": p.name})
        logger.info("Uploaded %s (%d bytes) → %s", p.name, p.stat().st_size, path)

    return project_id, files


async def kick_off(api_url: str, project_id: str) -> None:
    if not Config.INTERNAL_API_TOKEN:
        sys.exit("INTERNAL_API_TOKEN not set in env — needed to call the prod /api/v1/check endpoint.")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{api_url}/api/v1/check/{project_id}",
            headers={"Authorization": f"Bearer {Config.INTERNAL_API_TOKEN}"},
        )
        r.raise_for_status()
        logger.info("Kick-off POST → %s %s", r.status_code, r.json())


async def poll(project_id: str, file_names: dict[str, str]) -> None:
    """Poll str_results count + status until project leaves processing.

    Each tick logs how many rows have source_files populated and the per-file
    distribution of evidence — that's our x7h.8 acceptance signal.
    """
    sb = await get_supabase()
    last_count = -1
    while True:
        await asyncio.sleep(30)
        proj = await sb.table("projects").select("status, error").eq("id", project_id).single().execute()
        rows_resp = await sb.table("str_results").select("status, is_error, source_pages, source_files").eq("project_id", project_id).execute()
        rows = rows_resp.data or []
        n = len(rows)
        with_files = sum(1 for r in rows if r.get("source_files"))
        per_file_counts: dict[str, int] = {}
        for r in rows:
            for f in r.get("source_files") or []:
                per_file_counts[f["file_name"]] = per_file_counts.get(f["file_name"], 0) + 1
        if n != last_count:
            logger.info(
                "PROGRESS status=%s rows=%d source_files_populated=%d per_file=%s",
                proj.data["status"], n, with_files, per_file_counts,
            )
            last_count = n
        if proj.data["status"] in ("done", "failed"):
            return


async def show_examples(project_id: str) -> None:
    """Print 3 sample str_results rows that have source_files set, so the user can
    eyeball the shape: each entry should be {file_id, file_name, pages: [int]}."""
    sb = await get_supabase()
    rows_resp = await sb.table("str_results").select(
        "str_code, status, source_pages, source_files"
    ).eq("project_id", project_id).not_.is_("source_files", "null").limit(3).execute()
    rows = rows_resp.data or []
    if not rows:
        logger.warning("No rows with source_files set — x7h.8 may not have populated correctly!")
        return
    logger.info("=" * 70)
    logger.info("SAMPLE source_files entries (x7h.8 acceptance):")
    for r in rows:
        logger.info("  %s [%s]:", r["str_code"], r["status"])
        logger.info("    source_pages: %s", r["source_pages"])
        logger.info("    source_files: %s", json.dumps(r["source_files"], ensure_ascii=False))
    logger.info("=" * 70)


async def final_summary(project_id: str) -> dict:
    sb = await get_supabase()
    proj = await sb.table("projects").select("*").eq("id", project_id).single().execute()
    rows_resp = await sb.table("str_results").select("status, is_error, source_files").eq("project_id", project_id).execute()
    rows = rows_resp.data or []
    by_status: dict[str, int] = {}
    with_source_files = 0
    multi_file = 0
    errors = 0
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["source_files"]:
            with_source_files += 1
            if len(r["source_files"]) > 1:
                multi_file += 1
        if r["is_error"]:
            errors += 1
    return {
        "project_id": project_id,
        "name": proj.data["name"],
        "status": proj.data["status"],
        "error": proj.data.get("error"),
        "total_results": len(rows),
        "by_status": by_status,
        "is_error_count": errors,
        "with_source_files": with_source_files,
        "multi_file_evidence_rows": multi_file,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="*", type=Path, default=DEFAULT_PDFS)
    ap.add_argument("--api-url", default=DEFAULT_API)
    args = ap.parse_args()

    pdfs = args.pdfs or DEFAULT_PDFS
    for p in pdfs:
        if not p.exists():
            sys.exit(f"PDF not found: {p}")
    logger.info("Smoke target: %s, %d files", args.api_url, len(pdfs))

    started = time.time()
    project_id, files = await setup_project(pdfs)
    file_names = {f["id"]: f["file_name"] for f in files}

    await kick_off(args.api_url, project_id)

    try:
        await poll(project_id, file_names)
    except KeyboardInterrupt:
        logger.info("Interrupted — leaving project running on prod, exiting cleanly")
        return

    await show_examples(project_id)
    summary = await final_summary(project_id)
    elapsed = time.time() - started
    logger.info("=" * 70)
    logger.info("SMOKE DONE in %.0fs (%.1f min)", elapsed, elapsed / 60)
    for k, v in summary.items():
        logger.info("%-20s %s", k, v)
    logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
