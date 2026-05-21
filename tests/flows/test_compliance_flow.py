"""Tests for app.flows.compliance_flow.run_compliance_check.

Stateless pipeline: PDF bytes → JSON dict. PDFExtractor is patched to skip real
PDF parsing, and the Checker is a deterministic stub so we can assert on the
aggregation/summary shape without invoking the LLM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.flows import compliance_flow
from app.models.check_item import CheckItem, ComplianceResult
from app.services.chunker import TextChunk
from app.services.config_loader import load_all_check_items


def _make_result(item: CheckItem, status: str = "pass") -> ComplianceResult:
    return ComplianceResult(
        str_code=item.code,
        check_type=item.check_type,
        status=status,  # type: ignore[arg-type]
        comment="ok",
        confidence=0.9,
        is_error=False,
        source_pages=[1],
    )


@pytest.fixture
def stub_checker() -> AsyncMock:
    """Checker stub that returns a 'pass' result for any item."""
    checker = AsyncMock()

    async def _check(item: CheckItem, _chunks: list[TextChunk]) -> ComplianceResult:
        return _make_result(item, "pass")

    checker.check = AsyncMock(side_effect=_check)
    return checker


async def test_flow_returns_results_per_check_item(
    patch_pdf_extractor, no_inter_call_delay, stub_checker
) -> None:
    """One ComplianceResult per CheckItem; summary counts add up."""
    item_count = len(load_all_check_items())
    out = await compliance_flow.run_compliance_check(
        b"%PDF-fake", filename="test.pdf", checker=stub_checker
    )

    assert out["filename"] == "test.pdf"
    assert out["page_count"] == 2
    assert out["is_scanned"] is False
    assert out["warnings"] == []
    assert len(out["results"]) == item_count
    assert out["summary"]["total"] == item_count
    assert out["summary"]["pass"] == item_count
    assert out["summary"]["partial"] == 0
    assert out["summary"]["fail"] == 0
    assert out["summary"]["errors"] == 0


async def test_flow_summary_aggregates_mixed_statuses(
    patch_pdf_extractor, no_inter_call_delay
) -> None:
    """Summary counts pass/partial/fail/errors correctly across the run."""
    statuses = ["pass", "partial", "fail"]
    is_error_flags = [False, False, True, False]

    call_count = {"n": 0}

    async def _check(item: CheckItem, _chunks: list[TextChunk]) -> ComplianceResult:
        idx = call_count["n"]
        call_count["n"] += 1
        return ComplianceResult(
            str_code=item.code,
            check_type=item.check_type,
            status=statuses[idx % len(statuses)],  # type: ignore[arg-type]
            comment="x",
            confidence=0.5,
            is_error=is_error_flags[idx % len(is_error_flags)],
            source_pages=[],
        )

    checker = AsyncMock()
    checker.check = AsyncMock(side_effect=_check)

    item_count = len(load_all_check_items())
    out = await compliance_flow.run_compliance_check(b"%PDF-fake", checker=checker)

    summary = out["summary"]
    assert summary["total"] == item_count
    assert summary["pass"] + summary["partial"] + summary["fail"] == item_count
    # is_error flag cycles every 4th call.
    expected_errors = sum(1 for i in range(item_count) if is_error_flags[i % 4])
    assert summary["errors"] == expected_errors


async def test_flow_uses_provided_checker_not_default(
    patch_pdf_extractor, no_inter_call_delay, stub_checker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Custom checker is used; BasicChecker is never instantiated."""
    sentinel = {"basic_checker_constructed": False}

    class _FailIfCalled:
        def __init__(self, *args, **kwargs):
            sentinel["basic_checker_constructed"] = True
            raise AssertionError("BasicChecker should not be constructed")

    monkeypatch.setattr(compliance_flow, "BasicChecker", _FailIfCalled)
    item_count = len(load_all_check_items())
    await compliance_flow.run_compliance_check(b"%PDF-fake", checker=stub_checker)

    assert sentinel["basic_checker_constructed"] is False
    assert stub_checker.check.await_count == item_count


async def test_flow_passes_chunked_pages_to_checker(
    patch_pdf_extractor, no_inter_call_delay, fake_extracted_pdf
) -> None:
    """Verify the chunks handed to the checker came from the extracted PDF."""
    captured: dict[str, list[TextChunk]] = {}

    async def _check(item: CheckItem, chunks: list[TextChunk]) -> ComplianceResult:
        captured.setdefault("chunks", chunks)
        return _make_result(item)

    checker = AsyncMock()
    checker.check = AsyncMock(side_effect=_check)

    await compliance_flow.run_compliance_check(b"%PDF-fake", checker=checker)

    chunks = captured["chunks"]
    assert chunks, "expected at least one chunk"
    # The synthetic PDF has 2 pages; chunks should span those pages.
    pages_seen = set()
    for c in chunks:
        for p in range(c.start_page, c.end_page + 1):
            pages_seen.add(p)
    assert pages_seen.issubset({1, 2})
    assert pages_seen, "chunks reference no pages"


async def test_flow_invokes_on_progress_callback(
    patch_pdf_extractor, no_inter_call_delay, stub_checker
) -> None:
    """on_progress is called for extraction, chunking, items loaded, plus per-item."""
    messages: list[str] = []

    async def _on_progress(msg: str) -> None:
        messages.append(msg)

    item_count = len(load_all_check_items())
    await compliance_flow.run_compliance_check(
        b"%PDF-fake", checker=stub_checker, on_progress=_on_progress
    )

    # Pre-loop messages: PDF extracted, chunked, loaded items.
    assert any("PDF extracted" in m for m in messages)
    assert any("Chunked into" in m for m in messages)
    assert any("Loaded" in m for m in messages)
    # One message per check item.
    per_item_msgs = [m for m in messages if m.startswith("[")]
    assert len(per_item_msgs) == item_count


async def test_flow_supports_sync_progress_callback(
    patch_pdf_extractor, no_inter_call_delay, stub_checker
) -> None:
    """A non-async on_progress callable should also work (the flow guards on
    asyncio.iscoroutine before awaiting)."""
    messages: list[str] = []

    def _sync_progress(msg: str) -> None:
        messages.append(msg)

    await compliance_flow.run_compliance_check(
        b"%PDF-fake", checker=stub_checker, on_progress=_sync_progress
    )
    assert messages, "expected sync progress callback to receive messages"
