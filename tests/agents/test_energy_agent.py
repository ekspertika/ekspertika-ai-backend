"""Tests for the Stage 3 energy specialized agent (Epic 3 bvw.5)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents import energy_agent
from app.agents.energy_agent import EnergyAgent
from app.agents.routing import AGENT_REGISTRY, get_agent_for, register_agent
from app.models.check_item import CheckItem, ComplianceResult
from app.services.chunker import TextChunk


@pytest.fixture(autouse=True)
def _restore_registry():
    """Clean the registry per-test, then re-register EnergyAgent so other
    tests in this module rely on a known good baseline."""
    AGENT_REGISTRY.clear()
    register_agent(EnergyAgent())
    yield
    AGENT_REGISTRY.clear()


def _item(
    code: str = "STR 2.01.02:2016",
    check_type: str = "str",
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


def test_energy_agent_module_importable() -> None:
    assert energy_agent.EnergyAgent is EnergyAgent


def test_energy_agent_registered() -> None:
    agent = get_agent_for("energy")
    assert isinstance(agent, EnergyAgent)


def test_energy_agent_handles_own_routing() -> None:
    agent = EnergyAgent()
    item = _item("STR 2.01.02:2016")
    assert agent.handles(item, "energy") is True


def test_energy_agent_does_not_handle_other_routing() -> None:
    agent = EnergyAgent()
    item = _item("STR 2.05.01")
    assert agent.handles(item, "structural") is False
    assert agent.handles(item, "fire_safety") is False
    assert agent.handles(item, "sanitary") is False
    assert agent.handles(item, None) is False


@pytest.mark.asyncio
async def test_energy_agent_calls_llm_with_area_specific_prompt() -> None:
    agent = EnergyAgent()
    item = _item("STR 2.04.01:2018", keywords=["envelope"])
    chunks = [_chunk("U-value 0.20 W/m2K wall envelope", 1, 1)]

    with patch(
        "app.agents.base_agent.cc._call_compliance_llm",
        new=AsyncMock(return_value=_mock_result(item)),
    ) as mock_call:
        result = await agent.check(item, chunks)

    assert result.status == "pass"
    mock_call.assert_awaited_once()
    prompt = mock_call.await_args.kwargs["prompt"]
    assert prompt.startswith(agent.system_prompt_for(item.check_type))
    assert "energy-performance" in prompt.lower()


def test_energy_agent_str_framing_mentions_envelope_or_eurocode() -> None:
    """The energy agent's str framing must cite the building envelope code
    (STR 2.04.01) and explicitly mention envelope or Eurocode."""
    agent = EnergyAgent()
    framing = agent.system_prompt_for("str")
    assert "2.04.01" in framing
    lower = framing.lower()
    assert "envelope" in lower or "eurocode" in lower
