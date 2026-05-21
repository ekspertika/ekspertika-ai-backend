"""End-to-end smoke test for python-be-x7h.7.

Creates a project in Supabase, uploads a test PDF to storage, inserts a project_files row,
and runs the full Supabase-coupled compliance flow. Reports str_results count growth
and the final summary.

Usage:
  uv run python scripts/smoke_test.py [pdf_path]

Defaults to the smaller testiniai PDF if no path given.
"""

import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

from app.flows.supabase_flow import run_project_check
from app.integrations.supabase_client import get_supabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("smoke")


DEFAULT_PDF = Path(
    "/Users/foksler/Projects/my/roadto1m/ekspertika/nextjs-fe/testiniai-files/"
    "2. SP Zalioji 7 2025-10-30_ZIP.pdf"
)


async def setup_project(pdf_path: Path) -> tuple[str, str]:
    sb = await get_supabase()
    project_name = f"SMOKE TEST {pdf_path.stem} {time.strftime('%Y-%m-%d %H:%M:%S')}"
    project_resp = await (
        sb.table("projects")
        .insert({"name": project_name, "status": "pending"})
        .execute()
    )
    project_id: str = project_resp.data[0]["id"]
    logger.info("Created project %s (%s)", project_id, project_name)

    storage_path = f"smoke/{project_id}/{uuid.uuid4()}.pdf"
    pdf_bytes = pdf_path.read_bytes()
    await sb.storage.from_("project-files").upload(
        path=storage_path,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf"},
    )
    logger.info("Uploaded %d bytes to storage path: %s", len(pdf_bytes), storage_path)

    file_resp = await (
        sb.table("project_files")
        .insert(
            {
                "project_id": project_id,
                "file_name": pdf_path.name,
                "storage_path": storage_path,
            }
        )
        .execute()
    )
    file_id: str = file_resp.data[0]["id"]
    logger.info("Inserted project_files row %s", file_id)

    return project_id, storage_path


async def report_progress(project_id: str, total_items: int) -> None:
    """Poll str_results count every 30s until project status leaves 'processing'."""
    sb = await get_supabase()
    while True:
        await asyncio.sleep(30)
        proj = await (
            sb.table("projects").select("status, error").eq("id", project_id).single().execute()
        )
        results = await (
            sb.table("str_results")
            .select("status", count="exact", head=True)
            .eq("project_id", project_id)
            .execute()
        )
        logger.info(
            "PROGRESS: project=%s status=%s str_results=%d/%d",
            proj.data["status"],
            proj.data.get("status"),
            results.count or 0,
            total_items,
        )
        if proj.data["status"] in ("done", "failed"):
            return


async def final_summary(project_id: str) -> dict:
    sb = await get_supabase()
    proj = await (
        sb.table("projects").select("*").eq("id", project_id).single().execute()
    )
    rows = await (
        sb.table("str_results")
        .select("status, is_error, check_type")
        .eq("project_id", project_id)
        .execute()
    )
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    errors = 0
    for r in rows.data:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_type[r["check_type"]] = by_type.get(r["check_type"], 0) + 1
        if r["is_error"]:
            errors += 1
    return {
        "project_id": project_id,
        "name": proj.data["name"],
        "status": proj.data["status"],
        "error": proj.data.get("error"),
        "total_results": len(rows.data),
        "by_status": by_status,
        "by_type": by_type,
        "is_error_count": errors,
    }


async def main() -> None:
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")

    started = time.time()
    project_id, storage_path = await setup_project(pdf_path)

    progress_task = asyncio.create_task(report_progress(project_id, total_items=68))
    try:
        await run_project_check(project_id)
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

    elapsed = time.time() - started
    summary = await final_summary(project_id)
    logger.info("=" * 70)
    logger.info("SMOKE TEST DONE in %.0fs (%.1f min)", elapsed, elapsed / 60)
    logger.info("project_id:     %s", summary["project_id"])
    logger.info("status:         %s", summary["status"])
    if summary["error"]:
        logger.info("error:          %s", summary["error"])
    logger.info("total results:  %d", summary["total_results"])
    logger.info("by status:      %s", summary["by_status"])
    logger.info("by type:        %s", summary["by_type"])
    logger.info("api errors:     %d", summary["is_error_count"])
    logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
