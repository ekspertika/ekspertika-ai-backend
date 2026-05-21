"""Unit tests for the Stage 3 BaseAgent ABC + agent registry."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base_agent import BaseAgent
from app.agents.routing import (
    AGENT_REGISTRY,
    get_agent_for,
    register_agent,
)
from app.models.check_item import CheckItem, CheckType, ComplianceResult
from app.services.chunker import TextChunk


@pytest.fixture(autouse=True)
def _clear_registry():
    """Each test gets a clean registry — no cross-test pollution."""
    AGENT_REGISTRY.clear()
    yield
    AGENT_REGISTRY.clear()


def _item(code: str = "STR 2.05.01", check_type: str = "str") -> CheckItem:
    return CheckItem(code=code, title="t", category="c", check_type=check_type)  # type: ignore[arg-type]


def _chunk(text: str = "doc text", start: int = 1, end: int = 1) -> TextChunk:
    return TextChunk(index=0, text=text, start_page=start, end_page=end)


class _StubStructuralAgent(BaseAgent):
    """Tiny in-test subclass — replaces the deleted ``_example.py`` stub.

    Kept inline so this file stays decoupled from the concrete agents under
    ``app/agents/`` (those have their own dedicated test module).
    """

    AGENT_ROUTING = "structural"
    DESCRIPTION = "Stub structural agent for BaseAgent ABC tests."

    def system_prompt(self) -> str:
        return "stub structural system prompt"


def test_cannot_instantiate_abstract_class() -> None:
    with pytest.raises(TypeError):
        BaseAgent()  # type: ignore[abstract]


def test_subclass_must_implement_system_prompt() -> None:
    """A subclass missing ``system_prompt`` cannot be instantiated.

    Since bvw.8, ``check()`` has a default implementation on BaseAgent —
    ``system_prompt`` is the only remaining abstract method.
    """

    class PartialAgent(BaseAgent):
        AGENT_ROUTING = "partial"

    with pytest.raises(TypeError):
        PartialAgent()  # type: ignore[abstract]


def test_handles_default_predicate() -> None:
    agent = _StubStructuralAgent()
    item = _item()
    assert agent.handles(item, item_routing="structural") is True
    assert agent.handles(item, item_routing="energy") is False
    assert agent.handles(item, item_routing=None) is False


def test_register_and_lookup() -> None:
    agent = _StubStructuralAgent()
    register_agent(agent)
    assert get_agent_for("structural") is agent


def test_get_agent_for_unknown_routing_returns_none() -> None:
    assert get_agent_for("nonexistent") is None


def test_system_prompt_for_returns_system_prompt_by_default() -> None:
    """Default ``system_prompt_for`` falls back to ``system_prompt`` for
    every check_type — only agents that span multiple types override."""
    agent = _StubStructuralAgent()
    base = agent.system_prompt()
    for check_type in ("str", "law", "standard", "document"):
        assert agent.system_prompt_for(check_type) == base  # type: ignore[arg-type]


def test_system_prompt_for_override_takes_effect() -> None:
    """A subclass can override ``system_prompt_for`` to differentiate by
    check_type while keeping the generic ``system_prompt`` fallback."""

    class _SplitAgent(BaseAgent):
        AGENT_ROUTING = "split"

        def system_prompt(self) -> str:
            return "generic"

        def system_prompt_for(self, check_type: CheckType) -> str:
            if check_type == "standard":
                return "standard-only framing"
            return self.system_prompt()

    agent = _SplitAgent()
    assert agent.system_prompt_for("standard") == "standard-only framing"
    assert agent.system_prompt_for("str") == "generic"
    assert agent.system_prompt_for("law") == "generic"


@pytest.mark.asyncio
async def test_default_check_implementation_runs() -> None:
    """The default ``BaseAgent.check()`` pipeline threads
    ``system_prompt_for(item.check_type)`` through to the LLM call as the
    leading paragraph of the assembled prompt."""

    class _SplitAgent(BaseAgent):
        AGENT_ROUTING = "split"

        def system_prompt(self) -> str:
            return "generic-framing"

        def system_prompt_for(self, check_type: CheckType) -> str:
            if check_type == "standard":
                return "STANDARD-FRAMING-XYZ"
            return self.system_prompt()

    agent = _SplitAgent()
    item = _item("HN 42:2009", check_type="standard")
    chunks = [_chunk("project text", 1, 1)]

    sentinel = ComplianceResult(
        str_code=item.code,
        check_type=item.check_type,
        status="pass",
        comment="ok",
        confidence=0.9,
        is_error=False,
        source_pages=[1],
    )

    with patch(
        "app.agents.base_agent.cc._call_compliance_llm",
        new=AsyncMock(return_value=sentinel),
    ) as mock_call:
        result = await agent.check(item, chunks)

    assert result is sentinel
    mock_call.assert_awaited_once()
    prompt = mock_call.await_args.kwargs["prompt"]
    # The check_type-specific framing leads the prompt.
    assert prompt.startswith("STANDARD-FRAMING-XYZ")
    # And the standard typed body still follows it.
    assert "Lithuanian construction standards expert" in prompt
