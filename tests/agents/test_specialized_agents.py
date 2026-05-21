"""Tests for the Stage 3 specialized regulatory-area agents.

Covers ``structural_agent`` (bvw.2), ``fire_safety_agent`` (bvw.3) and
``sanitary_agent`` (bvw.4). Single combined module because the three agents
share fixtures and the tests are highly symmetric.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents import fire_safety_agent, sanitary_agent, structural_agent
from app.agents.fire_safety_agent import FireSafetyAgent
from app.agents.routing import AGENT_REGISTRY, get_agent_for, register_agent
from app.agents.sanitary_agent import SanitaryAgent
from app.agents.structural_agent import StructuralAgent
from app.models.check_item import CheckItem, ComplianceResult
from app.services.chunker import TextChunk


@pytest.fixture(autouse=True)
def _restore_registry():
    """Clean the registry per-test, then re-register the three concrete
    agents so other tests in this module rely on a known good baseline.

    Importing the agent modules at module load already registered them, but
    individual tests may clear or mutate the registry — restore on teardown
    so cross-module test order doesn't matter.
    """
    AGENT_REGISTRY.clear()
    register_agent(StructuralAgent())
    register_agent(FireSafetyAgent())
    register_agent(SanitaryAgent())
    yield
    AGENT_REGISTRY.clear()


def _item(
    code: str = "STR 2.05.01",
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


# Sentinel result returned by the mocked LLM helper. Keeps tests off the
# OpenAI client and off the network — the helper itself is exercised in
# tests/services/test_compliance_checker.py.
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


# --- module imports register the three agents ------------------------------


def test_specialized_agent_modules_importable() -> None:
    """Sanity: importing the agent modules surfaces the concrete classes."""
    assert structural_agent.StructuralAgent is StructuralAgent
    assert fire_safety_agent.FireSafetyAgent is FireSafetyAgent
    assert sanitary_agent.SanitaryAgent is SanitaryAgent


# --- structural_agent ------------------------------------------------------


def test_structural_agent_registered() -> None:
    agent = get_agent_for("structural")
    assert isinstance(agent, StructuralAgent)


def test_structural_agent_handles_own_routing() -> None:
    agent = StructuralAgent()
    item = _item("STR 2.05.01")
    assert agent.handles(item, "structural") is True


def test_structural_agent_does_not_handle_other_routing() -> None:
    agent = StructuralAgent()
    item = _item("STR 2.01.01(2)")
    assert agent.handles(item, "fire_safety") is False
    assert agent.handles(item, "sanitary") is False
    assert agent.handles(item, None) is False


@pytest.mark.asyncio
async def test_structural_agent_calls_llm_with_area_specific_prompt() -> None:
    agent = StructuralAgent()
    item = _item("STR 2.05.01", keywords=["concrete"])
    chunks = [_chunk("concrete load calculation", 1, 1)]

    with patch(
        "app.agents.base_agent.cc._call_compliance_llm",
        new=AsyncMock(return_value=_mock_result(item)),
    ) as mock_call:
        result = await agent.check(item, chunks)

    assert result.status == "pass"
    mock_call.assert_awaited_once()
    kwargs = mock_call.await_args.kwargs
    assert kwargs["item"] is item
    assert kwargs["prompt"].startswith(agent.system_prompt_for(item.check_type))
    # The area-specific framing must precede the standard typed prompt body.
    assert "structural-engineering" in kwargs["prompt"]


def test_structural_agent_str_framing_mentions_eurocode() -> None:
    """The structural-agent's str framing must explicitly cite the Eurocode
    authorities the agent claims (EC2 / EC3 / EC5 / EC6 mapped to STR 2.05.x)."""
    agent = StructuralAgent()
    framing = agent.system_prompt_for("str")
    assert "Eurocode 2" in framing or "EC2" in framing
    # And it should reference at least one specific STR 2.05.x sub-code.
    assert "2.05" in framing


# --- fire_safety_agent -----------------------------------------------------


def test_fire_safety_agent_registered() -> None:
    agent = get_agent_for("fire_safety")
    assert isinstance(agent, FireSafetyAgent)


def test_fire_safety_agent_handles_own_routing() -> None:
    agent = FireSafetyAgent()
    item = _item("STR 2.01.01(2)")
    assert agent.handles(item, "fire_safety") is True


def test_fire_safety_agent_does_not_handle_other_routing() -> None:
    agent = FireSafetyAgent()
    item = _item("STR 2.05.01")
    assert agent.handles(item, "structural") is False
    assert agent.handles(item, "sanitary") is False
    assert agent.handles(item, None) is False


@pytest.mark.asyncio
async def test_fire_safety_agent_calls_llm_with_area_specific_prompt() -> None:
    agent = FireSafetyAgent()
    item = _item("STR 2.01.12", keywords=["evacuation"])
    chunks = [_chunk("evacuation route", 1, 1)]

    with patch(
        "app.agents.base_agent.cc._call_compliance_llm",
        new=AsyncMock(return_value=_mock_result(item)),
    ) as mock_call:
        result = await agent.check(item, chunks)

    assert result.status == "pass"
    mock_call.assert_awaited_once()
    prompt = mock_call.await_args.kwargs["prompt"]
    assert prompt.startswith(agent.system_prompt_for(item.check_type))
    assert "fire-safety" in prompt


def test_fire_safety_agent_str_framing_mentions_rei_and_str() -> None:
    """The fire-safety agent's str framing must cite REI fire-resistance
    classes and the STR 2.01.01(2) authority it claims."""
    agent = FireSafetyAgent()
    framing = agent.system_prompt_for("str")
    assert "REI" in framing
    assert "2.01.01(2)" in framing


# --- sanitary_agent --------------------------------------------------------


def test_sanitary_agent_registered() -> None:
    agent = get_agent_for("sanitary")
    assert isinstance(agent, SanitaryAgent)


def test_sanitary_agent_handles_own_routing() -> None:
    agent = SanitaryAgent()
    item = _item("STR 2.01.01(3)")
    assert agent.handles(item, "sanitary") is True


def test_sanitary_agent_does_not_handle_other_routing() -> None:
    agent = SanitaryAgent()
    # Default predicate rejects unrelated routings...
    item = _item("STR 2.05.01")
    assert agent.handles(item, "structural") is False
    assert agent.handles(item, "fire_safety") is False
    # ...and a non-HN standard with no routing is also rejected.
    standard_item = _item("LST EN 1990", check_type="standard")
    assert agent.handles(standard_item, None) is False


def test_sanitary_agent_handles_hn_standards() -> None:
    """HN 42 / 69 / 98 are check_type='standard' and not in str_registry —
    sanitary_agent must claim them via its overridden handles()."""
    agent = SanitaryAgent()
    for code in ("HN 42:2009", "HN 69:2003", "HN 98:2014"):
        item = _item(code, check_type="standard")
        assert agent.handles(item, None) is True, f"sanitary should claim {code}"
        # Also true regardless of whatever routing the orchestrator passed.
        assert agent.handles(item, "structural") is True
    # Non-HN standards are NOT claimed.
    other = _item("LST EN 1990", check_type="standard")
    assert agent.handles(other, None) is False


@pytest.mark.asyncio
async def test_sanitary_agent_calls_llm_with_area_specific_prompt() -> None:
    agent = SanitaryAgent()
    item = _item("HN 42:2009", check_type="standard", keywords=["noise"])
    chunks = [_chunk("noise level 52 dB", 1, 1)]

    with patch(
        "app.agents.base_agent.cc._call_compliance_llm",
        new=AsyncMock(return_value=_mock_result(item)),
    ) as mock_call:
        result = await agent.check(item, chunks)

    assert result.status == "pass"
    mock_call.assert_awaited_once()
    prompt = mock_call.await_args.kwargs["prompt"]
    assert prompt.startswith(agent.system_prompt_for(item.check_type))
    assert "hygiene" in prompt.lower() or "HN" in prompt


def test_sanitary_agent_uses_str_framing_for_str_items() -> None:
    """For check_type='str' the sanitary agent uses essential-requirement
    framing, NOT the HN-numeric-limit framing."""
    agent = SanitaryAgent()
    framing = agent.system_prompt_for("str")
    assert "essential" in framing.lower()
    assert "2.01.01(3)" in framing
    # The HN-specific noise/light/vibration limits do NOT appear here.
    assert "55 dB" not in framing


def test_sanitary_agent_uses_standard_framing_for_hn_standards() -> None:
    """For check_type='standard' the sanitary agent uses HN-specific
    framing with named numeric limits."""
    agent = SanitaryAgent()
    framing = agent.system_prompt_for("standard")
    assert "HN 42" in framing
    assert "HN 69" in framing
    assert "HN 98" in framing
    # And the framing meaningfully differs from the str-path framing.
    assert framing != agent.system_prompt_for("str")
