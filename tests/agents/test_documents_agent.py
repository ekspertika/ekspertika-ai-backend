"""Tests for the Stage 3 documents specialized agent (Epic 3 bvw.6)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents import documents_agent
from app.agents.documents_agent import DocumentsAgent
from app.agents.routing import AGENT_REGISTRY, get_agent_for, register_agent
from app.models.check_item import CheckItem, ComplianceResult
from app.services.chunker import TextChunk


@pytest.fixture(autouse=True)
def _restore_registry():
    """Clean the registry per-test, then re-register DocumentsAgent so other
    tests in this module rely on a known good baseline."""
    AGENT_REGISTRY.clear()
    register_agent(DocumentsAgent())
    yield
    AGENT_REGISTRY.clear()


def _item(
    code: str = "Projektavimo užduotis",
    check_type: str = "document",
    keywords: list[str] | None = None,
) -> CheckItem:
    return CheckItem(
        code=code,
        title="t",
        category="c",
        check_type=check_type,  # type: ignore[arg-type]
        keywords=keywords or [],
    )


def _chunk(text: str = "doc text", start: int = 1, end: int = 1) -> TextChunk:
    return TextChunk(index=0, text=text, start_page=start, end_page=end)


def _mock_result(item: CheckItem) -> ComplianceResult:
    return ComplianceResult(
        str_code=item.code,
        check_type=item.check_type,
        status="pass",
        comment="ok",
        confidence=0.9,
        is_error=False,
        source_pages=[1],
    )


def test_documents_agent_module_importable() -> None:
    assert documents_agent.DocumentsAgent is DocumentsAgent


def test_documents_agent_registered() -> None:
    agent = get_agent_for("documents")
    assert isinstance(agent, DocumentsAgent)


def test_documents_agent_handles_every_document_check_type() -> None:
    """The override claims every check_type='document' item regardless of
    routing — that's the actual scoping mechanism for this agent."""
    agent = DocumentsAgent()
    for code in (
        "Projektavimo užduotis",
        "Įmonės registravimo pažymėjimas",
        "Topografinė nuotrauka",
    ):
        item = _item(code, check_type="document")
        # Routing intentionally varied — should be ignored.
        assert agent.handles(item, None) is True
        assert agent.handles(item, "documents") is True
        assert agent.handles(item, "structural") is True
        assert agent.handles(item, "energy") is True


def test_documents_agent_does_not_handle_non_documents() -> None:
    agent = DocumentsAgent()
    # STR / law / standard items are NOT claimed regardless of routing.
    str_item = _item("STR 2.05.01", check_type="str")
    assert agent.handles(str_item, "structural") is False
    assert agent.handles(str_item, "documents") is False
    law_item = _item("Statybos įstatymas", check_type="law")
    assert agent.handles(law_item, None) is False
    standard_item = _item("HN 42:2009", check_type="standard")
    assert agent.handles(standard_item, "sanitary") is False


@pytest.mark.asyncio
async def test_documents_agent_calls_llm_with_area_specific_prompt() -> None:
    agent = DocumentsAgent()
    item = _item(
        "Projektavimo užduotis",
        check_type="document",
        keywords=["užduotis"],
    )
    chunks = [_chunk("Projektavimo užduotis priimta", 1, 1)]

    with patch(
        "app.agents.base_agent.cc._call_compliance_llm",
        new=AsyncMock(return_value=_mock_result(item)),
    ) as mock_call:
        result = await agent.check(item, chunks)

    assert result.status == "pass"
    mock_call.assert_awaited_once()
    prompt = mock_call.await_args.kwargs["prompt"]
    assert prompt.startswith(agent.system_prompt_for(item.check_type))
    assert "project-documentation" in prompt.lower() or "mandatory" in prompt.lower()


def test_documents_agent_framing_mentions_mandatory_package() -> None:
    """The documents agent must frame itself as a presence check over the
    mandatory project-package documents and name at least a few of them."""
    agent = DocumentsAgent()
    framing = agent.system_prompt_for("document")
    lower = framing.lower()
    assert "mandatory" in lower
    assert "16" in framing
    # Lithuanian document names must be cited so the model has named anchors.
    assert "projektavimo užduotis" in lower
    assert "topografinė nuotrauka" in lower
