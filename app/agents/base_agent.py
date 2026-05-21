"""Abstract base class for Stage 3 specialized regulatory-area agents.

Each subclass owns a regulatory area (str_registry_entry.agent_routing) and
decides whether a given CheckItem is in scope. The orchestrator (bvw.7) routes
items by agent_routing first, then falls back to a default agent for items
outside every specialized scope.

A BaseAgent implements the Checker Protocol (see
`app.services.compliance_checker.Checker`) — it is a drop-in replacement for
BasicChecker / RAGChecker behind the orchestrator.

Concrete agents (bvw.2..bvw.6 — structural / fire_safety / sanitary / energy /
documents) subclass this. Since bvw.8 the base class also provides the default
``check()`` pipeline (select chunks → build prompt → call LLM → post-process),
so concrete agents shrink to a few declarations: ``AGENT_ROUTING``,
``DESCRIPTION``, ``system_prompt``, an optional ``system_prompt_for`` per-
check_type override, and an optional ``handles`` override.
"""

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from openai import AsyncOpenAI

from app.models.check_item import CheckItem, CheckType, ComplianceResult
from app.services import compliance_checker as cc
from app.services.chunker import TextChunk, select_top_chunks
from config.config import Config

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base for Stage 3 specialized regulatory-area agents.

    Subclasses customize:
      • the prompt template (area-specific framing via ``system_prompt`` and
        optionally per-check_type via ``system_prompt_for``)
      • the in-scope predicate (default: matches ``AGENT_ROUTING``)

    The default ``check()`` covers the standard pipeline used by all five
    specialized agents — select top chunks, build the typed prompt, call
    ``_call_compliance_llm``, return the verdict. Override only when an agent
    needs a custom retrieval / prompt-assembly strategy.
    """

    # Subclasses set this — the str_registry_entry.agent_routing they handle.
    AGENT_ROUTING: ClassVar[str]

    # Human-readable description for orchestrator logs / status pages.
    DESCRIPTION: ClassVar[str] = ""

    def __init__(self, model: str | None = None) -> None:
        self.client = AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
        self.model = model or Config.COMPLIANCE_MODEL

    @abstractmethod
    def system_prompt(self) -> str:
        """Generic area-specific system prompt header used by the LLM call.

        This is the agent's default framing — used for every ``check_type``
        the agent sees unless ``system_prompt_for`` overrides it.

        Example for ``structural_agent``::

            "You are a Lithuanian construction structural-engineering compliance "
            "expert specializing in STR 2.05.x (concrete, steel, masonry, timber, "
            "foundations) and STR 2.02.x (residential buildings)."
        """

    def system_prompt_for(self, check_type: CheckType) -> str:
        """Per-check_type framing override.

        Default: returns ``self.system_prompt()``. Subclasses override only
        when their handling of one ``check_type`` benefits from different
        framing — e.g. ``sanitary_agent`` wants different vocabulary for STR
        items (essential hygiene requirement) vs HN-coded standards (specific
        noise / light / vibration limit values).
        """
        return self.system_prompt()

    def handles(self, item: CheckItem, item_routing: str | None = None) -> bool:
        """Default scope predicate: agent owns the item if its routing matches.

        Override for cross-cutting agents (e.g. ``documents_agent`` handles all
        ``check_type='document'`` items regardless of routing).

        ``item_routing`` is looked up by the orchestrator from the STR registry;
        passed as ``None`` for non-STR items (laws / standards / documents).
        """
        return item_routing == self.AGENT_ROUTING

    async def check(self, item: CheckItem, chunks: list[TextChunk]) -> ComplianceResult:
        """Default compliance-check pipeline.

        Selects top chunks, builds the standard typed prompt, prepends the
        per-check_type system framing, and delegates to the shared
        ``_call_compliance_llm`` helper so post-processing semantics
        (source_pages, status, fail-clearing) stay identical across the
        BasicChecker / RAGChecker / agent code paths.

        Override only when an agent needs a custom retrieval / prompt-
        assembly strategy. Currently no concrete agent overrides this.
        """
        relevant = select_top_chunks(chunks, item.keywords, top_n=3)
        excerpts = cc._build_excerpts(relevant)
        chunk_pages = cc._collect_chunk_pages(relevant)

        # Specific agent framings ("Eurocode 2/3/5/6", "REI fire-resistance
        # classes", etc.) made the model treat every cited authority as a
        # required keyword in the project doc — when not found, it returned
        # 'fail' instead of 'partial'. The leniency instruction (same fix as
        # e6n.10 for RAGChecker) explicitly tells it indirect / conceptual
        # matches still count toward 'partial'. See bvw.10.
        base_prompt = cc._PROMPT_BUILDERS[item.check_type](item, excerpts)
        prompt = (
            f"{self.system_prompt_for(item.check_type)}\n\n"
            f"{base_prompt}\n"
            f"{cc._LENIENCY_INSTRUCTION}"
        )

        try:
            return await cc._call_compliance_llm(
                client=self.client,
                model=self.model,
                item=item,
                prompt=prompt,
                chunk_pages=chunk_pages,
            )
        except Exception as exc:
            logger.error(
                "%s failure for %s: %s", type(self).__name__, item.code, exc
            )
            return ComplianceResult(
                str_code=item.code,
                check_type=item.check_type,
                status="fail",
                comment="Technical error — API call failed after all retries.",
                confidence=0,
                is_error=True,
                source_pages=[],
            )
