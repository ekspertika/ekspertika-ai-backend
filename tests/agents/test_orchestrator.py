"""Tests for the Stage 3 AgentOrchestrator (Epic 3 bvw.7).

We deliberately don't import the real specialized agents in this module —
that would pollute AGENT_REGISTRY with their concrete instances. Instead
each test installs tiny ``_FakeAgent`` subclasses into the registry and
verifies the orchestrator's routing decisions in isolation.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.agents import orchestrator as orch_module
from app.agents.base_agent import BaseAgent
from app.agents.orchestrator import AgentOrchestrator, _build_default_fallback
from app.agents.routing import AGENT_REGISTRY
from app.models.check_item import CheckItem, ComplianceResult
from app.models.str_registry_entry import STRRegistryEntry
from app.services.chunker import TextChunk
from app.services.compliance_checker import BasicChecker, Checker


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a clean registry and clears it on teardown."""
    AGENT_REGISTRY.clear()
    yield
    AGENT_REGISTRY.clear()


@pytest.fixture
def patched_orchestrator(monkeypatch):
    """Build an AgentOrchestrator without triggering the real agent imports.

    The orchestrator's ``_import_agents`` would normally pull in
    structural / fire_safety / sanitary / energy / documents and register
    them — that pollutes the registry our tests want to control. Stub it
    with a no-op so the registry only contains what the test puts there.
    """
    monkeypatch.setattr(AgentOrchestrator, "_import_agents", staticmethod(lambda: None))


# --- helpers ---------------------------------------------------------------


def _item(
    code: str = "STR 2.05.01",
    check_type: str = "str",
) -> CheckItem:
    return CheckItem(code=code, title="t", category="c", check_type=check_type)  # type: ignore[arg-type]


def _chunk(text: str = "doc text", start: int = 1, end: int = 1) -> TextChunk:
    return TextChunk(index=0, text=text, start_page=start, end_page=end)


def _result(code: str, check_type: str = "str") -> ComplianceResult:
    return ComplianceResult(
        str_code=code,
        check_type=check_type,  # type: ignore[arg-type]
        status="pass",
        comment="ok",
        confidence=0.9,
        is_error=False,
        source_pages=[1],
    )


def _make_fake_agent(routing: str, claim_predicate=None):
    """Build a registered fake agent with a mocked async ``check`` method.

    ``claim_predicate`` overrides the default ``handles`` (matching by
    AGENT_ROUTING) — pass a callable ``(item, item_routing) -> bool`` to
    exercise probe-based routing.
    """

    class _FakeAgent(BaseAgent):
        AGENT_ROUTING = routing

        def system_prompt(self) -> str:
            return f"fake {routing} prompt"

        async def check(self, item, chunks):
            return _result(item.code, item.check_type)

        if claim_predicate is not None:
            def handles(self, item, item_routing=None):  # noqa: D401
                return claim_predicate(item, item_routing)

    agent = _FakeAgent()
    # Replace check with an AsyncMock so individual tests can assert calls.
    agent.check = AsyncMock(side_effect=lambda item, chunks: _result(item.code, item.check_type))
    return agent


def _registry_entry(code: str, agent_routing: str) -> STRRegistryEntry:
    return STRRegistryEntry(
        code=code,
        title="t",
        category="performance",
        area="structural" if agent_routing == "structural" else "energy",
        etar_url=None,
        etar_search_query="q",
        last_amended_year=2020,
        filename="x.pdf",
        agent_routing=agent_routing,  # type: ignore[arg-type]
    )


# --- routing rules ---------------------------------------------------------


@pytest.mark.asyncio
async def test_documents_routes_to_documents_agent(patched_orchestrator):
    docs = _make_fake_agent(
        "documents",
        claim_predicate=lambda item, _r: item.check_type == "document",
    )
    AGENT_REGISTRY["documents"] = docs

    orch = AgentOrchestrator(fallback=BasicChecker())
    item = _item("Projektavimo užduotis", check_type="document")
    result = await orch.check(item, [_chunk()])

    assert result.status == "pass"
    docs.check.assert_awaited_once()
    assert docs.check.await_args.args[0] is item


@pytest.mark.asyncio
async def test_str_routes_via_registry(monkeypatch, patched_orchestrator):
    structural = _make_fake_agent("structural")
    energy = _make_fake_agent("energy")
    AGENT_REGISTRY["structural"] = structural
    AGENT_REGISTRY["energy"] = energy

    monkeypatch.setattr(
        orch_module,
        "get_by_code",
        lambda code: _registry_entry(code, "energy") if code == "STR 2.04.01:2018" else None,
    )

    orch = AgentOrchestrator(fallback=BasicChecker())
    item = _item("STR 2.04.01:2018", check_type="str")
    await orch.check(item, [_chunk()])

    energy.check.assert_awaited_once()
    structural.check.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_str_falls_back(monkeypatch, patched_orchestrator):
    structural = _make_fake_agent("structural")
    AGENT_REGISTRY["structural"] = structural

    monkeypatch.setattr(orch_module, "get_by_code", lambda code: None)

    fallback = AsyncMock(spec=Checker)
    fallback.check = AsyncMock(return_value=_result("STR 9.99.99", "str"))

    orch = AgentOrchestrator(fallback=fallback)
    item = _item("STR 9.99.99", check_type="str")
    await orch.check(item, [_chunk()])

    fallback.check.assert_awaited_once()
    structural.check.assert_not_called()


@pytest.mark.asyncio
async def test_standard_routes_via_handles_probe(patched_orchestrator):
    """A fake sanitary agent claims HN-prefixed standards through handles()."""

    def claims_hn(item, _routing):
        return item.check_type == "standard" and item.code.upper().startswith("HN ")

    sanitary = _make_fake_agent("sanitary", claim_predicate=claims_hn)
    structural = _make_fake_agent("structural")
    AGENT_REGISTRY["sanitary"] = sanitary
    AGENT_REGISTRY["structural"] = structural

    orch = AgentOrchestrator(fallback=BasicChecker())
    item = _item("HN 42:2009", check_type="standard")
    await orch.check(item, [_chunk()])

    sanitary.check.assert_awaited_once()
    structural.check.assert_not_called()


@pytest.mark.asyncio
async def test_law_falls_back(patched_orchestrator):
    """Laws have no agent — they go to the fallback checker."""
    structural = _make_fake_agent("structural")
    AGENT_REGISTRY["structural"] = structural

    fallback = AsyncMock(spec=Checker)
    fallback.check = AsyncMock(return_value=_result("Statybos įstatymas", "law"))

    orch = AgentOrchestrator(fallback=fallback)
    item = _item("Statybos įstatymas", check_type="law")
    await orch.check(item, [_chunk()])

    fallback.check.assert_awaited_once()
    structural.check.assert_not_called()


# --- fallback selection ----------------------------------------------------


def test_orchestrator_uses_basic_fallback_by_default(monkeypatch, patched_orchestrator):
    monkeypatch.setattr("config.config.Config.USE_RAG", False)
    fallback = _build_default_fallback()
    assert isinstance(fallback, BasicChecker)


def test_orchestrator_uses_rag_fallback_when_use_rag(monkeypatch, patched_orchestrator):
    monkeypatch.setattr("config.config.Config.USE_RAG", True)

    # The real RAGChecker import pulls in chromadb. Patch the module-level
    # symbol so _build_default_fallback() returns a sentinel without hitting
    # the chroma import.
    sentinel = object()

    class _StubRAGChecker:
        def __new__(cls, *args, **kwargs):
            return sentinel

    # _build_default_fallback does ``from app.services.compliance_checker
    # import RAGChecker`` — patch that target.
    monkeypatch.setattr(
        "app.services.compliance_checker.RAGChecker",
        _StubRAGChecker,
        raising=False,
    )
    fallback = _build_default_fallback()
    assert fallback is sentinel


# --- protocol conformance --------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_implements_checker_protocol(patched_orchestrator):
    """Duck-type check: AgentOrchestrator.check matches the Checker contract.

    ``Checker`` is a structural Protocol (not @runtime_checkable), so we
    verify conformance by calling ``check()`` and asserting the return type.
    """
    structural = _make_fake_agent("structural")
    AGENT_REGISTRY["structural"] = structural

    orch = AgentOrchestrator(fallback=BasicChecker())

    # Mimics how the flows call it: orch.check(item, chunks) → ComplianceResult.
    with patch.object(orch, "_route", return_value=structural):
        result = await orch.check(_item("STR 2.05.01"), [_chunk()])
    assert isinstance(result, ComplianceResult)
