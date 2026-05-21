"""Tests for app.flows.supabase_flow.

The Supabase async client is mocked via the `mock_supabase` fixture (see
conftest). PDFExtractor is patched too so the storage download can return any
bytes — extraction returns a synthetic ExtractedPDF.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.flows import supabase_flow
from app.models.check_item import CheckItem, ComplianceResult
from app.services.chunker import TextChunk
from app.services.config_loader import load_all_check_items


def _result(item: CheckItem, status: str = "pass") -> ComplianceResult:
    return ComplianceResult(
        str_code=item.code,
        check_type=item.check_type,
        status=status,  # type: ignore[arg-type]
        comment="ok",
        confidence=0.85,
        is_error=False,
        source_pages=[1, 2],
    )


@pytest.fixture
def stub_checker() -> AsyncMock:
    checker = AsyncMock()

    async def _check(item: CheckItem, _chunks: list[TextChunk]) -> ComplianceResult:
        return _result(item, "pass")

    checker.check = AsyncMock(side_effect=_check)
    return checker


@pytest.fixture
def one_file_row() -> list[dict]:
    return [
        {
            "id": "file-1",
            "project_id": "proj-1",
            "storage_path": "proj-1/doc.pdf",
            "file_name": "doc.pdf",
        }
    ]


@pytest.fixture
def two_file_rows() -> list[dict]:
    """Multi-file project: two files, each contributing 2 pages to the synthetic
    ExtractedPDF (the patch_pdf_extractor fixture returns the same 2-page PDF for
    both downloads). Global page index then runs 1..4 — pages 1-2 from file A,
    pages 3-4 from file B."""
    return [
        {
            "id": "file-A",
            "project_id": "proj-1",
            "storage_path": "proj-1/A.pdf",
            "file_name": "A.pdf",
        },
        {
            "id": "file-B",
            "project_id": "proj-1",
            "storage_path": "proj-1/B.pdf",
            "file_name": "B.pdf",
        },
    ]


# ---------- run_project_check happy path ----------


async def test_run_project_check_marks_processing_then_done(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, stub_checker, one_file_row
) -> None:
    """The flow flips status='processing' first, then 'done' last."""
    mock_supabase.set_files(one_file_row)

    await supabase_flow.run_project_check("proj-1", checker=stub_checker)

    project_writes = [
        w for w in mock_supabase.writes_for("projects", "update")
    ]
    statuses = [w["payload"].get("status") for w in project_writes]
    assert statuses[0] == "processing"
    assert statuses[-1] == "done"
    # First write must clear any previous error.
    assert project_writes[0]["payload"].get("error") is None


async def test_run_project_check_inserts_one_row_per_check_item(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, stub_checker, one_file_row
) -> None:
    """One str_results insert per CheckItem."""
    mock_supabase.set_files(one_file_row)

    await supabase_flow.run_project_check("proj-1", checker=stub_checker)

    inserts = mock_supabase.writes_for("str_results", "insert")
    assert len(inserts) == len(load_all_check_items())


async def test_run_project_check_writes_correct_row_shape(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, stub_checker, one_file_row
) -> None:
    """Inserted str_results rows must carry every required field."""
    mock_supabase.set_files(one_file_row)

    await supabase_flow.run_project_check("proj-1", checker=stub_checker)

    inserts = mock_supabase.writes_for("str_results", "insert")
    payload = inserts[0]["payload"]
    expected_keys = {
        "project_id",
        "str_code",
        "check_type",
        "status",
        "comment",
        "confidence",
        "is_error",
        "source_pages",
        "source_files",
    }
    assert expected_keys == set(payload.keys())
    assert payload["project_id"] == "proj-1"
    assert payload["status"] == "pass"
    assert payload["is_error"] is False
    assert payload["source_pages"] == [1, 2]
    # Single-file: every page lives under file-1.
    assert payload["source_files"] == [
        {"file_id": "file-1", "file_name": "doc.pdf", "pages": [1, 2]}
    ]


async def test_run_project_check_persists_extracted_text_per_file(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, stub_checker, one_file_row
) -> None:
    """Each project_files row gets its extracted_text persisted by id."""
    mock_supabase.set_files(one_file_row)

    await supabase_flow.run_project_check("proj-1", checker=stub_checker)

    file_updates = mock_supabase.writes_for("project_files", "update")
    assert len(file_updates) == 1
    assert "extracted_text" in file_updates[0]["payload"]
    assert ("id", "file-1") in file_updates[0]["eq"]


# ---------- multi-file source_files (python-be-x7h.8) ----------


async def test_run_project_check_multi_file_splits_source_files_per_file(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, two_file_rows
) -> None:
    """Two files × 2 pages each → global pages 1-2 (file A), 3-4 (file B). When
    the checker reports source_pages=[2, 4], the row must split that into
    [{file A: [2]}, {file B: [2]}]."""
    mock_supabase.set_files(two_file_rows)

    multi_page_checker = AsyncMock()

    async def _check(item, _chunks):
        return ComplianceResult(
            str_code=item.code,
            check_type=item.check_type,
            status="pass",
            comment="ok",
            confidence=0.85,
            is_error=False,
            source_pages=[2, 4],
        )

    multi_page_checker.check = AsyncMock(side_effect=_check)

    await supabase_flow.run_project_check("proj-1", checker=multi_page_checker)

    inserts = mock_supabase.writes_for("str_results", "insert")
    payload = inserts[0]["payload"]
    # source_pages keeps global numbering (legacy contract).
    assert payload["source_pages"] == [2, 4]
    # source_files de-aliases by file in upload order, with per-file 1-based pages.
    assert payload["source_files"] == [
        {"file_id": "file-A", "file_name": "A.pdf", "pages": [2]},
        {"file_id": "file-B", "file_name": "B.pdf", "pages": [2]},
    ]


async def test_run_project_check_multi_file_omits_files_with_no_evidence(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, two_file_rows
) -> None:
    """A file that contributed no source_pages should not appear in source_files
    — the FE shouldn't render an empty 'A.pdf:' badge."""
    mock_supabase.set_files(two_file_rows)

    file_b_only = AsyncMock()

    async def _check(item, _chunks):
        return ComplianceResult(
            str_code=item.code,
            check_type=item.check_type,
            status="pass",
            comment="from file B only",
            confidence=0.9,
            is_error=False,
            source_pages=[3],  # global page 3 → file B page 1
        )

    file_b_only.check = AsyncMock(side_effect=_check)

    await supabase_flow.run_project_check("proj-1", checker=file_b_only)

    payload = mock_supabase.writes_for("str_results", "insert")[0]["payload"]
    assert payload["source_files"] == [
        {"file_id": "file-B", "file_name": "B.pdf", "pages": [1]}
    ]


async def test_run_project_check_fail_status_clears_source_files(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, two_file_rows
) -> None:
    """status=fail rows get source_pages=None and therefore source_files=None
    too — failed checks have no evidence by definition (mirrors x7h.9 semantics)."""
    mock_supabase.set_files(two_file_rows)

    fail_checker = AsyncMock()

    async def _check(item, _chunks):
        return ComplianceResult(
            str_code=item.code,
            check_type=item.check_type,
            status="fail",
            comment="not found",
            confidence=0.3,
            is_error=False,
            source_pages=[],
        )

    fail_checker.check = AsyncMock(side_effect=_check)

    await supabase_flow.run_project_check("proj-1", checker=fail_checker)

    payload = mock_supabase.writes_for("str_results", "insert")[0]["payload"]
    assert payload["source_pages"] is None
    assert payload["source_files"] is None


# ---------- run_project_check failure paths ----------


async def test_run_project_check_persists_failure_to_projects_error(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, one_file_row
) -> None:
    """When the checker raises, status flips to 'failed' with the error message
    persisted before the exception is re-raised."""
    mock_supabase.set_files(one_file_row)

    boom_checker = AsyncMock()
    boom_checker.check = AsyncMock(side_effect=RuntimeError("checker exploded"))

    with pytest.raises(RuntimeError, match="checker exploded"):
        await supabase_flow.run_project_check("proj-1", checker=boom_checker)

    project_writes = mock_supabase.writes_for("projects", "update")
    failure_writes = [w for w in project_writes if w["payload"].get("status") == "failed"]
    assert failure_writes, "expected at least one status='failed' write"
    assert failure_writes[-1]["payload"]["error"] == "checker exploded"


async def test_run_project_check_no_files_raises(
    mock_supabase, patch_pdf_extractor, no_inter_call_delay, stub_checker
) -> None:
    """Empty project_files list → RuntimeError, status='failed'."""
    mock_supabase.set_files([])

    with pytest.raises(RuntimeError, match="No project_files"):
        await supabase_flow.run_project_check("proj-1", checker=stub_checker)

    failure_writes = [
        w for w in mock_supabase.writes_for("projects", "update")
        if w["payload"].get("status") == "failed"
    ]
    assert failure_writes


# ---------- retry_errored_checks ----------


async def test_retry_errored_checks_only_updates_error_rows(
    mock_supabase, no_inter_call_delay
) -> None:
    """retry_errored_checks updates only the rows returned by the is_error filter."""
    # Pick a real check code so items_by_code lookup succeeds.
    items = load_all_check_items()
    target_codes = [items[0].code, items[1].code]
    error_rows = [
        {"id": "row-A", "str_code": target_codes[0], "check_type": items[0].check_type},
        {"id": "row-B", "str_code": target_codes[1], "check_type": items[1].check_type},
    ]
    mock_supabase.set_files(
        [{"id": "f1", "extracted_text": "alpha beta gamma delta"}]
    )
    mock_supabase.set_error_rows(error_rows)

    checker = AsyncMock()

    async def _check(item: CheckItem, _chunks):
        return _result(item, "pass")

    checker.check = AsyncMock(side_effect=_check)

    await supabase_flow.retry_errored_checks("proj-1", checker=checker)

    str_updates = mock_supabase.writes_for("str_results", "update")
    updated_ids = {dict(w["eq"]).get("id") for w in str_updates}
    assert updated_ids == {"row-A", "row-B"}
    # No insert path during retries.
    assert mock_supabase.writes_for("str_results", "insert") == []
    # Each update payload carries the post-check fields.
    sample = str_updates[0]["payload"]
    assert {"status", "comment", "confidence", "is_error", "source_pages"} <= sample.keys()
    # Final status flips to 'done'.
    assert mock_supabase.writes_for("projects", "update")[-1]["payload"]["status"] == "done"


async def test_retry_errored_checks_no_errors_marks_done_immediately(
    mock_supabase, no_inter_call_delay, stub_checker
) -> None:
    """When the error_rows query returns [], the checker is never invoked and
    status flips straight to 'done'."""
    mock_supabase.set_files(
        [{"id": "f1", "extracted_text": "alpha beta gamma"}]
    )
    mock_supabase.set_error_rows([])

    await supabase_flow.retry_errored_checks("proj-1", checker=stub_checker)

    stub_checker.check.assert_not_called()
    project_writes = mock_supabase.writes_for("projects", "update")
    statuses = [w["payload"].get("status") for w in project_writes]
    assert statuses[0] == "processing"
    assert statuses[-1] == "done"
    # No str_results inserts/updates at all (selects are allowed).
    assert mock_supabase.writes_for("str_results", "insert") == []
    assert mock_supabase.writes_for("str_results", "update") == []


async def test_retry_errored_checks_no_extracted_text_raises(
    mock_supabase, no_inter_call_delay, stub_checker
) -> None:
    """If no project_files row has extracted_text, the retry path errors out and
    persists status='failed'."""
    mock_supabase.set_files([{"id": "f1", "extracted_text": None}])

    with pytest.raises(RuntimeError, match="No extracted_text"):
        await supabase_flow.retry_errored_checks("proj-1", checker=stub_checker)

    failure_writes = [
        w for w in mock_supabase.writes_for("projects", "update")
        if w["payload"].get("status") == "failed"
    ]
    assert failure_writes
