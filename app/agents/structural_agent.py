"""Structural-engineering specialized agent (Epic 3 bvw.2).

Owns the structural regulatory area: STR 1.x administrative regulations
covering project structure / expertise, STR 2.02.x residential buildings,
and STR 2.05.x structural design (concrete, steel, masonry, timber,
foundations) — each STR 2.05 sub-code maps to a specific Eurocode.

Inherits the default ``check()`` pipeline from ``BaseAgent``: select chunks
→ prepend ``system_prompt_for(check_type)`` → call ``_call_compliance_llm``.
Self-registers via ``register_agent`` at module import time so the
orchestrator (bvw.7) finds it without hard-coding agent imports.
"""

from app.agents.base_agent import BaseAgent
from app.agents.routing import register_agent

_STRUCTURAL_SYSTEM_PROMPT = (
    "You are a Lithuanian construction structural-engineering compliance "
    "expert. You specialize in STR 2.05.05 (concrete — Eurocode 2), STR "
    "2.05.06 (timber — Eurocode 5), STR 2.05.08 (steel — Eurocode 3), STR "
    "2.05.09 (masonry — Eurocode 6), STR 2.02.01 (residential building "
    "requirements), and STR 1.04.04 / STR 1.05.01 (project structure and "
    "expertise). When evaluating compliance, focus on: load calculations, "
    "Eurocode references (EC2 concrete, EC3 steel, EC5 timber, EC6 masonry), "
    "material classes, foundation / geotechnical reports, and structural-"
    "system documentation."
)


class StructuralAgent(BaseAgent):
    """Specialized agent for structural-engineering regulations."""

    AGENT_ROUTING = "structural"
    DESCRIPTION = (
        "Structural engineering: STR 1.x (admin), 2.02.x (buildings), "
        "2.05.x (structures — Eurocodes 2/3/5/6)."
    )

    def system_prompt(self) -> str:
        return _STRUCTURAL_SYSTEM_PROMPT


# Self-register at import. The orchestrator (bvw.7) looks up agents by
# AGENT_ROUTING via app.agents.routing.get_agent_for().
register_agent(StructuralAgent())
