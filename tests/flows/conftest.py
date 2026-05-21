"""Shared fixtures for tests/flows.

Centralises the Supabase mock so individual tests focus on assertions, not on
wiring up the chained `.table().update().eq().execute()` MagicMock calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.pdf_extractor import ExtractedPDF


def _make_table_chain(execute_payload: Any) -> MagicMock:
    """Build a chainable table mock whose `.execute()` returns `execute_payload`.

    Supports both the read pattern (`.select(...).eq(...).execute()`) and the
    write patterns (`.insert(...).execute()`, `.update(...).eq(...).execute()`).
    """
    chain = MagicMock()
    chain.execute = AsyncMock(return_value=execute_payload)
    # Each builder method returns the same chain so further calls keep chaining.
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.update.return_value = chain
    chain.eq.return_value = chain
    return chain


@pytest.fixture
def fake_extracted_pdf() -> ExtractedPDF:
    """Synthetic PDF: 2 pages, enough text to produce at least one chunk."""
    return ExtractedPDF(
        filename="document.pdf",
        page_count=2,
        pages={1: "alpha beta gamma", 2: "delta epsilon zeta"},
        is_scanned=False,
        warnings=[],
    )


@pytest.fixture
def patch_pdf_extractor(monkeypatch: pytest.MonkeyPatch, fake_extracted_pdf: ExtractedPDF):
    """Patch PDFExtractor.extract_from_bytes in both flow modules."""

    def _fake_extract(self, pdf_bytes: bytes, filename: str = "document.pdf") -> ExtractedPDF:
        # Mirror the real method: keep the caller-supplied filename.
        fake_extracted_pdf.filename = filename
        return fake_extracted_pdf

    monkeypatch.setattr(
        "app.flows.compliance_flow.PDFExtractor.extract_from_bytes", _fake_extract
    )
    monkeypatch.setattr(
        "app.flows.supabase_flow.PDFExtractor.extract_from_bytes", _fake_extract
    )
    return fake_extracted_pdf


@pytest.fixture
def no_inter_call_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the 1s sleep between LLM calls so the suite stays fast."""

    async def _noop(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr("app.flows.compliance_flow.asyncio.sleep", _noop)
    monkeypatch.setattr("app.flows.supabase_flow.asyncio.sleep", _noop)


class SupabaseMockState:
    """Recording mock for the Supabase async client.

    Captures every write so tests can assert on the exact call sequence and
    payload shape. Override `.set_files(...)` and `.set_error_rows(...)` per
    test to drive the read paths.
    """

    def __init__(self) -> None:
        self.client = MagicMock()
        # Recorded writes. Each entry is (table, op, payload, eq_args).
        self.writes: list[dict[str, Any]] = []
        # Tunable read responses.
        self._files: list[dict[str, Any]] = []
        self._error_rows: list[dict[str, Any]] = []
        self._download_bytes: bytes = b"%PDF-fake-bytes"
        # Set up the dispatch.
        self.client.table = MagicMock(side_effect=self._table)
        self.client.storage.from_ = MagicMock(return_value=self._storage_bucket())

    def set_files(self, files: list[dict[str, Any]]) -> None:
        self._files = files

    def set_error_rows(self, rows: list[dict[str, Any]]) -> None:
        self._error_rows = rows

    def set_download_bytes(self, data: bytes) -> None:
        self._download_bytes = data

    def _storage_bucket(self) -> MagicMock:
        bucket = MagicMock()
        bucket.download = AsyncMock(side_effect=lambda _path: self._download_bytes)
        return bucket

    def _table(self, table_name: str) -> MagicMock:
        recorder = self  # closure capture

        # Build a fresh chain per call so each .table() invocation is independent.
        chain = MagicMock()
        # Track the most recent op (insert/update) and payload, plus eq filters.
        state: dict[str, Any] = {"op": None, "payload": None, "eq": []}

        def _insert(payload):
            state["op"] = "insert"
            state["payload"] = payload
            return chain

        def _update(payload):
            state["op"] = "update"
            state["payload"] = payload
            return chain

        def _select(*args, **kwargs):
            state["op"] = "select"
            state["payload"] = args
            return chain

        def _eq(col, val):
            state["eq"].append((col, val))
            return chain

        async def _execute():
            recorder.writes.append(
                {
                    "table": table_name,
                    "op": state["op"],
                    "payload": state["payload"],
                    "eq": list(state["eq"]),
                }
            )
            # Read responses.
            if table_name == "project_files" and state["op"] == "select":
                return SimpleNamespace(data=recorder._files)
            if table_name == "str_results" and state["op"] == "select":
                return SimpleNamespace(data=recorder._error_rows)
            return SimpleNamespace(data=None)

        chain.insert = MagicMock(side_effect=_insert)
        chain.update = MagicMock(side_effect=_update)
        chain.select = MagicMock(side_effect=_select)
        chain.eq = MagicMock(side_effect=_eq)
        chain.execute = AsyncMock(side_effect=_execute)
        return chain

    # Convenience accessors for assertions.
    def writes_for(self, table: str, op: str | None = None) -> list[dict[str, Any]]:
        return [
            w for w in self.writes
            if w["table"] == table and (op is None or w["op"] == op)
        ]


@pytest.fixture
def mock_supabase(monkeypatch: pytest.MonkeyPatch) -> SupabaseMockState:
    """Patch `get_supabase` in supabase_flow to return a recording mock."""
    state = SupabaseMockState()

    async def _get_supabase():
        return state.client

    monkeypatch.setattr("app.flows.supabase_flow.get_supabase", _get_supabase)
    return state
