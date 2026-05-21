"""Mandatory-documents specialized agent (Epic 3 bvw.6).

Owns the 16 mandatory project-package documents — items with
``check_type='document'`` (e.g. ``Projektavimo užduotis``, ``Įmonės
registravimo pažymėjimas``). These do NOT live in ``str_registry.json``;
they're loaded from compliance.config.json with no ``agent_routing``.

Because of that the registry-driven routing in the orchestrator never produces
``"documents"`` for them. We override ``handles()`` to claim *every* item
whose ``check_type`` is ``"document"``, regardless of routing.

Inherits the default ``check()`` pipeline from ``BaseAgent``. Self-registers
via ``register_agent`` at module import time so the orchestrator (bvw.7)
finds it without hard-coding agent imports.
"""

from app.agents.base_agent import BaseAgent
from app.agents.routing import register_agent
from app.models.check_item import CheckItem

_DOCUMENTS_SYSTEM_PROMPT = (
    "You are a Lithuanian construction project-documentation auditor. Your "
    "job is to verify the presence of mandatory project-package documents "
    "in the uploaded set. There are 16 mandatory documents: projektavimo "
    "užduotis, įmonės registravimo pažymėjimas, PV / PDV atestatai, "
    "civilinės atsakomybės draudimas, topografinė nuotrauka, projekto "
    "vadovo paskyrimas, gyventojų sprendimas, NT registro išrašas (statiniai "
    "+ sklypas), kadastro byla, įgaliojimas, rašytiniai pritarimai, "
    "investicijų planas, energinio naudingumo sertifikatas, architektūros "
    "reikalavimai, programinės įrangos sąrašas, and the project's signed "
    "table of contents. When evaluating compliance, focus on: presence of "
    "file names that match the required document, table-of-contents entries "
    "listing the document, signatures or stamps confirming the document's "
    "existence, and explicit cross-references from the body of the project "
    "to the required document. This is a *presence* check — you are not "
    "judging the document's content, only whether it is in the package or "
    "explicitly referenced."
)


class DocumentsAgent(BaseAgent):
    """Specialized agent for the 16 mandatory project-package documents."""

    # Set for consistency / observability — but the actual scoping happens in
    # ``handles()``: any check_type='document' item belongs to us regardless
    # of routing.
    AGENT_ROUTING = "documents"
    DESCRIPTION = (
        "Mandatory project documents: claims every check_type='document' "
        "item regardless of agent_routing. 16 mandatory package documents."
    )

    def system_prompt(self) -> str:
        return _DOCUMENTS_SYSTEM_PROMPT

    def handles(self, item: CheckItem, item_routing: str | None = None) -> bool:
        """Claim everything with check_type='document'.

        The 16 mandatory documents have no entry in ``str_registry.json``, so
        the orchestrator's registry-driven routing never produces our
        AGENT_ROUTING value for them. This override is the actual scoping
        mechanism for this agent — ``item_routing`` is intentionally
        ignored.
        """
        return item.check_type == "document"


# Self-register at import. The orchestrator (bvw.7) looks up agents by
# AGENT_ROUTING via app.agents.routing.get_agent_for().
register_agent(DocumentsAgent())
