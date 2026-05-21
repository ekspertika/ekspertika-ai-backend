"""Fire-safety specialized agent (Epic 3 bvw.3).

Owns the fire_safety regulatory area: STR 2.01.01(2):1999 (essential fire-
safety requirements). NOTE: STR 2.01.12:2024 was historically the fire-safety
of buildings code, but as of the 2024 amendment that designation was
reassigned to climatology — see python-be-bvw.9. For fire-resistance
assessment we therefore lean on STR 2.01.01(2) plus the related GSPR / GPGST
norms.

Inherits the default ``check()`` pipeline from ``BaseAgent``. Self-registers
via ``register_agent`` at module import time so the orchestrator (bvw.7)
finds it without hard-coding agent imports.
"""

from app.agents.base_agent import BaseAgent
from app.agents.routing import register_agent

_FIRE_SAFETY_SYSTEM_PROMPT = (
    "You are a Lithuanian construction fire-safety compliance expert. You "
    "specialize in STR 2.01.01(2):1999 essential fire-safety requirements, "
    "together with the related GSPR / GPGST norms. (Note: STR 2.01.12:2024 "
    "now covers climatology after the 2024 amendment — for fire-resistance "
    "assessment lean on STR 2.01.01(2) only.) When evaluating compliance, "
    "focus on: fire-resistance classes (REI 30 / REI 60 / REI 120), "
    "evacuation routes and exits, smoke and fire compartmentalization, "
    "sprinkler / fire-detection / alarm systems, fire-load classes, distance "
    "to the nearest fire station, and access for fire-fighting vehicles."
)


class FireSafetyAgent(BaseAgent):
    """Specialized agent for fire-safety regulations."""

    AGENT_ROUTING = "fire_safety"
    DESCRIPTION = (
        "Fire safety: STR 2.01.01(2):1999 essential fire-safety requirements "
        "plus related GSPR / GPGST norms."
    )

    def system_prompt(self) -> str:
        return _FIRE_SAFETY_SYSTEM_PROMPT


# Self-register at import. The orchestrator (bvw.7) looks up agents by
# AGENT_ROUTING via app.agents.routing.get_agent_for().
register_agent(FireSafetyAgent())
