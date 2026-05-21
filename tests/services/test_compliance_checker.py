"""Tests for app.services.compliance_checker.

Covers both BasicChecker and RAGChecker. All OpenAI calls are mocked. The
TPM rate-limiter is bypassed by patching ``token_budget.acquire`` to a
no-op — those concerns are tested separately in test_rate_limiter.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.check_item import CheckItem
from app.services import compliance_checker as cc
from app.services.chunker import TextChunk
from app.services.retriever import RetrievedChunk


def _fake_completion(payload: dict) -> SimpleNamespace:
    """Mimic an OpenAI chat-completions response."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))
        ]
    )


def _make_checker(payload: dict) -> tuple[cc.BasicChecker, AsyncMock]:
    create_mock = AsyncMock(return_value=_fake_completion(payload))
    fake_client = MagicMock()
    fake_client.chat.completions.create = create_mock

    checker = cc.BasicChecker.__new__(cc.BasicChecker)
    checker.client = fake_client
    checker.model = "gpt-4o-mini-test"
    return checker, create_mock


def _item(check_type: str = "str") -> CheckItem:
    return CheckItem(
        code="STR 1.01.01:2024",
        title="Test STR",
        category="Test",
        check_type=check_type,  # type: ignore[arg-type]
        requirement_text="Some requirement.",
        keywords=["test"],
    )


def _chunks() -> list[TextChunk]:
    return [
        TextChunk(index=0, text="alpha test content one", start_page=1, end_page=2),
        TextChunk(index=1, text="beta test content two", start_page=3, end_page=3),
    ]


@pytest.fixture(autouse=True)
def _bypass_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_tokens: int) -> None:
        return None

    monkeypatch.setattr(cc.token_budget, "acquire", _noop)


class TestSourcePagesPostProcessing:
    @pytest.mark.asyncio
    async def test_fail_status_clears_source_pages(self) -> None:
        """Regression for python-be-x7h.9.

        The model sometimes returns the full list of scanned pages in
        ``source_pages`` even when status=fail. The post-processor must clear
        them — failed checks have no evidence by definition.
        """
        checker, _ = _make_checker(
            {
                "status": "fail",
                "comment": "no evidence",
                "confidence": 0.2,
                "source_pages": [1, 2, 3, 4, 5],
            }
        )
        result = await checker.check(_item(), _chunks())
        assert result.status == "fail"
        assert result.source_pages == []

    @pytest.mark.asyncio
    async def test_pass_status_keeps_source_pages(self) -> None:
        checker, _ = _make_checker(
            {
                "status": "pass",
                "comment": "evidence on page 3",
                "confidence": 0.9,
                "source_pages": [3],
            }
        )
        result = await checker.check(_item(), _chunks())
        assert result.status == "pass"
        assert result.source_pages == [3]

    @pytest.mark.asyncio
    async def test_partial_status_keeps_source_pages(self) -> None:
        checker, _ = _make_checker(
            {
                "status": "partial",
                "comment": "partial coverage",
                "confidence": 0.6,
                "source_pages": [2, 3],
            }
        )
        result = await checker.check(_item(), _chunks())
        assert result.status == "partial"
        assert result.source_pages == [2, 3]

    @pytest.mark.asyncio
    async def test_fallback_to_chunk_pages_when_ai_returns_empty(self) -> None:
        """If the model returns an empty source_pages on a non-fail status,
        fall back to the union of chunk page ranges so the FE still has a
        page link to highlight."""
        checker, _ = _make_checker(
            {
                "status": "pass",
                "comment": "evidence implied",
                "confidence": 0.7,
                "source_pages": [],
            }
        )
        result = await checker.check(_item(), _chunks())
        assert result.status == "pass"
        assert result.source_pages == [1, 2, 3]


class TestCitationPostProcessing:
    """e6n.8 — citation field flows through into ComplianceResult and respects the
    same 'no evidence on fail' rule as source_pages."""

    @pytest.mark.asyncio
    async def test_citation_parsed_from_response(self) -> None:
        checker, _ = _make_checker(
            {
                "status": "pass",
                "comment": "Article 4.3 satisfied",
                "confidence": 0.8,
                "source_pages": [3],
                "citation": "STR 2.02.01:2004, 4.3 str.",
            }
        )
        result = await checker.check(_item(), _chunks())
        assert result.citation == "STR 2.02.01:2004, 4.3 str."

    @pytest.mark.asyncio
    async def test_citation_missing_field_is_none(self) -> None:
        """Backwards-compat: older prompts / models that don't return ``citation``
        must still produce a valid ComplianceResult."""
        checker, _ = _make_checker(
            {
                "status": "partial",
                "comment": "partial coverage",
                "confidence": 0.5,
                "source_pages": [2],
            }
        )
        result = await checker.check(_item(), _chunks())
        assert result.citation is None

    @pytest.mark.asyncio
    async def test_citation_cleared_on_fail(self) -> None:
        """Mirrors the source_pages=[] rule — a failed check has no evidence so
        it can't carry a citation, even if the model emitted one."""
        checker, _ = _make_checker(
            {
                "status": "fail",
                "comment": "no relevant content",
                "confidence": 0.3,
                "source_pages": [],
                "citation": "STR 2.02.01:2004, 4.3 str.",
            }
        )
        result = await checker.check(_item(), _chunks())
        assert result.status == "fail"
        assert result.citation is None

    @pytest.mark.asyncio
    async def test_blank_citation_normalises_to_none(self) -> None:
        """Empty string and whitespace-only citation collapse to None so downstream
        consumers (Excel reporter, FE) only see two states: ``str`` or ``None``."""
        checker, _ = _make_checker(
            {
                "status": "pass",
                "comment": "ok",
                "confidence": 0.7,
                "source_pages": [1],
                "citation": "   ",
            }
        )
        result = await checker.check(_item(), _chunks())
        assert result.citation is None


class TestPromptSchemaIncludesFailRule:
    """Static assertions on the four prompt builders — the schema must
    explicitly state that source_pages is [] when status=fail."""

    def test_str_prompt_states_fail_rule(self) -> None:
        prompt = cc._build_str_prompt(_item("str"), "[ex]")
        assert "source_pages MUST be []" in prompt

    def test_law_prompt_states_fail_rule(self) -> None:
        prompt = cc._build_law_prompt(_item("law"), "[ex]")
        assert "source_pages MUST be []" in prompt

    def test_standard_prompt_states_fail_rule(self) -> None:
        prompt = cc._build_standard_prompt(_item("standard"), "[ex]")
        assert "source_pages MUST be []" in prompt

    def test_document_prompt_states_fail_rule(self) -> None:
        prompt = cc._build_document_prompt(_item("document"), "[ex]")
        assert "source_pages MUST be []" in prompt


# ---------------------------------------------------------------------------
# RAGChecker
# ---------------------------------------------------------------------------


def _retrieved(
    *,
    str_code: str = "STR 1.01.01:2024",
    text: str = "regulation excerpt body",
    article: str | None = "4.3",
    start_page: int = 12,
    end_page: int = 12,
    similarity: float = 0.85,
) -> RetrievedChunk:
    return RetrievedChunk(
        str_code=str_code,
        text=text,
        start_page=start_page,
        end_page=end_page,
        article=article,
        similarity=similarity,
    )


def _make_rag_checker(
    *,
    payload: dict,
    retrieved: list[RetrievedChunk],
) -> tuple[cc.RAGChecker, AsyncMock, MagicMock]:
    """Build a RAGChecker with mocked OpenAI client + mocked retriever.

    Bypasses __init__ so we don't construct a real STRRetriever (which would
    try to import chromadb).
    """
    create_mock = AsyncMock(return_value=_fake_completion(payload))
    fake_client = MagicMock()
    fake_client.chat.completions.create = create_mock

    fake_retriever = MagicMock()
    fake_retriever.retrieve_for_str = AsyncMock(return_value=retrieved)
    fake_retriever.retrieve_open = AsyncMock(return_value=retrieved)

    checker = cc.RAGChecker.__new__(cc.RAGChecker)
    checker.client = fake_client
    checker.model = "gpt-4o-mini-test"
    checker.retriever = fake_retriever
    checker.top_k = 5
    return checker, create_mock, fake_retriever


class TestRAGChecker:
    @pytest.mark.asyncio
    async def test_rag_checker_passes_str_code_filter_for_str_items(self) -> None:
        """For check_type='str', the retriever's str-filtered method must be
        called with the item.code and the per-call top_k.
        """
        checker, _, fake_retriever = _make_rag_checker(
            payload={
                "status": "pass",
                "comment": "ok per article 4.3",
                "confidence": 0.8,
                "source_pages": [3],
            },
            retrieved=[_retrieved(str_code="STR 1.01.01:2024")],
        )
        await checker.check(_item("str"), _chunks())

        fake_retriever.retrieve_for_str.assert_awaited_once()
        args, kwargs = fake_retriever.retrieve_for_str.call_args
        assert args[0] == "STR 1.01.01:2024"
        assert kwargs.get("top_k") == 5
        fake_retriever.retrieve_open.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ctype", ["law", "standard", "document"])
    async def test_rag_checker_uses_open_retrieve_for_law_standard_document(
        self, ctype: str
    ) -> None:
        """Non-STR items use the unfiltered semantic search path."""
        checker, _, fake_retriever = _make_rag_checker(
            payload={
                "status": "pass",
                "comment": "ok",
                "confidence": 0.7,
                "source_pages": [1],
            },
            retrieved=[_retrieved()],
        )
        await checker.check(_item(ctype), _chunks())

        fake_retriever.retrieve_open.assert_awaited_once()
        fake_retriever.retrieve_for_str.assert_not_called()

    @pytest.mark.asyncio
    async def test_rag_checker_falls_back_to_basic_prompt_when_no_chunks_retrieved(self) -> None:
        """Empty retrieval → prompt is the plain BasicChecker prompt and
        confidence is unclamped. Earlier versions added a "no regulation
        excerpts" note + 0.4 confidence cap, which biased the model toward
        'fail' for the 23 of 30 STRs not yet ingested. See e6n.10.
        """
        checker, create_mock, _ = _make_rag_checker(
            payload={
                "status": "pass",
                "comment": "from training knowledge",
                "confidence": 0.9,
                "source_pages": [3],
            },
            retrieved=[],
        )
        result = await checker.check(_item("str"), _chunks())

        prompt = create_mock.call_args.kwargs["messages"][0]["content"]
        # No "no regulation excerpts" note — pure BasicChecker behaviour.
        assert "No regulation excerpts available" not in prompt
        # No regulation-section header either.
        assert "STR regulation excerpts" not in prompt
        # No leniency / citation instructions when there's no regulation text.
        assert cc._LENIENCY_INSTRUCTION not in prompt
        assert cc._REGULATION_CITE_INSTRUCTION not in prompt
        # Confidence flows through untouched (no ceiling).
        assert result.confidence == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_rag_checker_includes_leniency_instruction_when_chunks_present(self) -> None:
        """With retrieved regulation text the prompt must explicitly tell the
        model to count indirect/conceptual matches as 'partial' — counters the
        over-literal behaviour found in the e6n.10 smoke test.
        """
        checker, create_mock, _ = _make_rag_checker(
            payload={
                "status": "partial",
                "comment": "indirect compliance",
                "confidence": 0.7,
                "source_pages": [4],
            },
            retrieved=[_retrieved()],
        )
        await checker.check(_item("str"), _chunks())

        prompt = create_mock.call_args.kwargs["messages"][0]["content"]
        assert cc._LENIENCY_INSTRUCTION in prompt
        assert cc._REGULATION_CITE_INSTRUCTION in prompt

    @pytest.mark.asyncio
    async def test_rag_checker_propagates_status_correctly(self) -> None:
        """Happy path: status flows through, source_pages logic mirrors
        BasicChecker (FAIL clears pages; PASS keeps the AI-returned ones).
        """
        # PASS path
        checker, _, _ = _make_rag_checker(
            payload={
                "status": "pass",
                "comment": "evidence on p.3",
                "confidence": 0.9,
                "source_pages": [3],
            },
            retrieved=[_retrieved()],
        )
        result = await checker.check(_item("str"), _chunks())
        assert result.status == "pass"
        assert result.source_pages == [3]
        assert result.confidence == pytest.approx(0.9)
        assert result.is_error is False

        # FAIL path: source_pages MUST be cleared (mirrors BasicChecker).
        checker, _, _ = _make_rag_checker(
            payload={
                "status": "fail",
                "comment": "no evidence",
                "confidence": 0.2,
                "source_pages": [1, 2, 3],
            },
            retrieved=[_retrieved()],
        )
        result = await checker.check(_item("str"), _chunks())
        assert result.status == "fail"
        assert result.source_pages == []

    @pytest.mark.asyncio
    async def test_rag_checker_includes_regulation_excerpts_in_prompt(self) -> None:
        """When retrieval yields chunks, the prompt must contain the
        regulation header (str_code + article + page) and the cite-the-article
        instruction at the end.
        """
        checker, create_mock, _ = _make_rag_checker(
            payload={
                "status": "pass",
                "comment": "ok",
                "confidence": 0.7,
                "source_pages": [3],
            },
            retrieved=[
                _retrieved(
                    str_code="STR 2.02.01:2004",
                    article="4.3",
                    start_page=12,
                    end_page=12,
                    text="article 4.3 regulation body",
                )
            ],
        )
        await checker.check(_item("str"), _chunks())
        prompt = create_mock.call_args.kwargs["messages"][0]["content"]

        assert "STR regulation excerpts" in prompt
        assert "[STR 2.02.01:2004, Article 4.3, Page 12]" in prompt
        assert "article 4.3 regulation body" in prompt
        assert "Cite the STR article" in prompt
