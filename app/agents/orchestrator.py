"""Routes CheckItems to specialized agents (Epic 3 bvw.7).

Implements the same ``Checker`` Protocol as ``BasicChecker`` / ``RAGChecker`` —
drop-in replacement, swappable via the flow's ``checker=`` argument or the
new ``Config.USE_AGENTS`` env switch.

Routing rules:
  1. ``check_type='document'``  → documents_agent (claims by check_type)
  2. ``check_type='str'``       → str_registry_loader.get_by_code → agent_routing
  3. ``check_type='standard'``  → first agent whose handles() returns True
                                  (sanitary_agent claims HN entries)
  4. Anything unmatched         → fallback Checker (default: BasicChecker
                                  so laws + unmapped standards still work)

Items are processed sequentially to keep TPM throttling effective. The
orchestrator could parallelize across agents, but the rate limiter is
process-wide so true parallelism gains little — and stability beats
throughput at the current 60-90s/item average.
"""

import logging

from app.agents.base_agent import BaseAgent
from app.agents.routing import AGENT_REGISTRY, get_agent_for
from app.models.check_item import CheckItem, ComplianceResult
from app.services.chunker import TextChunk
from app.services.compliance_checker import BasicChecker, Checker
from app.services.str_registry_loader import get_by_code
from config.config import Config

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Top-level Checker that routes each item to a specialized agent.

    The orchestrator falls back to ``BasicChecker`` (or ``RAGChecker`` when
    ``Config.USE_RAG`` is true) for items no agent claims — laws, non-HN
    standards, and STR codes whose ``agent_routing`` isn't owned yet still
    get a verdict.
    """

    def __init__(self, fallback: Checker | None = None) -> None:
        # Trigger agent module imports so register_agent() runs. Each agent
        # module self-registers at import time.
        self._import_agents()
        self.fallback: Checker = fallback or _build_default_fallback()

    @staticmethod
    def _import_agents() -> None:
        # Side-effect imports — exhaustive list, easier than dynamic discovery.
        from app.agents import (  # noqa: F401
            documents_agent,
            energy_agent,
            fire_safety_agent,
            sanitary_agent,
            structural_agent,
        )

    def _route(self, item: CheckItem) -> BaseAgent | None:
        # 1. documents go to documents_agent regardless of registry routing.
        if item.check_type == "document":
            return get_agent_for("documents")

        # 2. STR codes — registry-driven routing.
        if item.check_type == "str":
            entry = get_by_code(item.code)
            if entry is not None:
                agent = get_agent_for(entry.agent_routing)
                if agent is not None:
                    return agent
                # Routing string doesn't match any registered agent — fall
                # through to handles() probe so future agents can claim it
                # without a registry update.
                return self._probe(item, item_routing=entry.agent_routing)
            # Unknown STR — try the handles() probe before giving up.
            return self._probe(item, item_routing=None)

        # 3. standards / laws — probe agents to see if anyone claims it
        # (sanitary_agent picks up HN-prefixed standards here).
        return self._probe(item, item_routing=None)

    def _probe(self, item: CheckItem, item_routing: str | None) -> BaseAgent | None:
        for agent in AGENT_REGISTRY.values():
            if agent.handles(item, item_routing):
                return agent
        return None

    async def check(self, item: CheckItem, chunks: list[TextChunk]) -> ComplianceResult:
        agent = self._route(item)
        if agent is None:
            logger.debug("No agent for %s [%s] — falling back", item.code, item.check_type)
            return await self.fallback.check(item, chunks)

        logger.debug("Routing %s [%s] → %s", item.code, item.check_type, type(agent).__name__)
        return await agent.check(item, chunks)


def _build_default_fallback() -> Checker:
    """Pick BasicChecker or RAGChecker for fallback based on Config.USE_RAG."""
    if Config.USE_RAG:
        # Lazy import — RAGChecker pulls in chromadb (the [rag] extra).
        from app.services.compliance_checker import RAGChecker

        return RAGChecker()
    return BasicChecker()
