"""Energy-performance specialized agent (Epic 3 bvw.5).

Owns the energy regulatory area: STR 2.01.02:2016 (energy performance and
certification), STR 2.01.06:2009 (engineering systems / lightning
protection), STR 2.01.07:2003 (noise protection — overlaps the sanitary
area but classified under energy in the registry), and STR 2.04.01:2018
(walls, roofs, windows / building envelope).

Inherits the default ``check()`` pipeline from ``BaseAgent``. Self-registers
via ``register_agent`` at module import time so the orchestrator (bvw.7)
finds it without hard-coding agent imports.
"""

from app.agents.base_agent import BaseAgent
from app.agents.routing import register_agent

_ENERGY_SYSTEM_PROMPT = (
    "You are a Lithuanian construction energy-performance compliance expert. "
    "You specialize in STR 2.01.02:2016 (pastatų energinio naudingumo "
    "projektavimas ir sertifikavimas — energy performance design and "
    "certification), STR 2.01.06:2009 (engineering systems / lightning "
    "protection), STR 2.01.07:2003 (apsauga nuo triukšmo — noise protection, "
    "overlaps the sanitary area), and STR 2.04.01:2018 (pastatų atitvaros — "
    "building envelope: walls, roofs, windows). When evaluating compliance, "
    "focus on: thermal performance and U-values of the building envelope, "
    "heating / ventilation / cooling capacity calculations, energy class "
    "certification (A++ / A+ / A / B), renewable-energy share, Eurocode-"
    "based thermal calculations, and lightning-protection design where "
    "relevant."
)


class EnergyAgent(BaseAgent):
    """Specialized agent for energy-performance regulations."""

    AGENT_ROUTING = "energy"
    DESCRIPTION = (
        "Energy performance: STR 2.01.02 (energy class), 2.01.06 "
        "(engineering / lightning), 2.01.07 (noise — overlaps sanitary), "
        "2.04.01 (building envelope)."
    )

    def system_prompt(self) -> str:
        return _ENERGY_SYSTEM_PROMPT


# Self-register at import. The orchestrator (bvw.7) looks up agents by
# AGENT_ROUTING via app.agents.routing.get_agent_for().
register_agent(EnergyAgent())
