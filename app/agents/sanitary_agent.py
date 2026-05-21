"""Sanitary / hygiene specialized agent (Epic 3 bvw.4).

Owns the sanitary regulatory area: STR 2.01.01(3):1999 (hygiene and health
essential requirement) plus the HN 42 / HN 69 / HN 98 hygiene-norm
standards (noise, natural light, vibration). HN entries are stored as
``check_type="standard"`` and are NOT in ``str_registry.json`` — so we
override ``handles()`` to claim any standard whose code starts with
``HN ``.

This agent is the only one that meaningfully spans two ``check_type`` values
(``str`` for STR 2.01.01(3) + ``standard`` for HN entries), so it overrides
``system_prompt_for`` to give each path different framing — the STR path
talks about the *essential hygiene requirement* in the abstract, the
standard path cites the specific HN limit values.

Inherits the default ``check()`` pipeline from ``BaseAgent``. Self-registers
via ``register_agent`` at module import time so the orchestrator (bvw.7)
finds it without hard-coding agent imports.
"""

from app.agents.base_agent import BaseAgent
from app.agents.routing import register_agent
from app.models.check_item import CheckItem, CheckType

_SANITARY_GENERIC_PROMPT = (
    "You are a Lithuanian construction sanitary / hygiene compliance "
    "expert. You specialize in STR 2.01.01(3):1999 (hygiene and health "
    "essential requirement) and the HN-series hygiene norms — HN 42 "
    "(environmental noise), HN 69 (natural light), HN 98 (vibration). When "
    "evaluating compliance, focus on: noise levels and sound insulation "
    "between flats, daylight factor and natural-light requirements, "
    "vibration limits, indoor air quality, and ventilation rates."
)

_SANITARY_STR_PROMPT = (
    "You are a Lithuanian construction sanitary / hygiene compliance "
    "expert. For this STR check you are evaluating against STR 2.01.01(3):"
    "1999 — the *essential hygiene and health requirement* for buildings. "
    "Focus on whether the project documentation demonstrates that the "
    "building, as a whole, meets the hygiene essential requirement: "
    "adequate ventilation, daylight access, drinking-water supply, "
    "drainage, prevention of damp / mould, and absence of harmful "
    "substances. This is a high-level requirement check — specific dB or "
    "lux numbers belong to the HN standards (HN 42 / 69 / 98), not here."
)

_SANITARY_STANDARD_PROMPT = (
    "You are a Lithuanian hygiene-norm (HN) compliance expert. The HN "
    "norms are issued by the Ministry of Health (sveikatos apsaugos "
    "ministras) and set specific numeric limits. For this check, evaluate "
    "the project against: HN 42:2009 environmental noise (residential "
    "limits ≤ 55 dB(A) day / ≤ 45 dB(A) night), HN 69:2003 natural-light "
    "coefficients (KEO ≥ 0.5%), and HN 98:2000 vibration limits. Focus on "
    "whether the project explicitly cites the HN code and demonstrates "
    "that the relevant numeric limit is met or designed for."
)


_HN_PREFIX = "HN "


class SanitaryAgent(BaseAgent):
    """Specialized agent for sanitary / hygiene regulations + HN norms."""

    AGENT_ROUTING = "sanitary"
    DESCRIPTION = (
        "Sanitary / hygiene: STR 2.01.01(3):1999 plus HN 42 / 69 / 98 "
        "hygiene norms (noise, light, vibration)."
    )

    def system_prompt(self) -> str:
        return _SANITARY_GENERIC_PROMPT

    def system_prompt_for(self, check_type: CheckType) -> str:
        """Different framing for STR (essential requirement) vs HN standards
        (specific numeric limits) — the two have different vocabulary and
        authorities."""
        if check_type == "str":
            return _SANITARY_STR_PROMPT
        if check_type == "standard":
            return _SANITARY_STANDARD_PROMPT
        return _SANITARY_GENERIC_PROMPT

    def handles(self, item: CheckItem, item_routing: str | None = None) -> bool:
        """Claim sanitary STRs (default predicate) plus HN-coded standards.

        HN 42 / 69 / 98 are ``check_type="standard"`` and not in
        ``str_registry.json``, so the registry-driven routing lookup never
        produces the ``"sanitary"`` value for them. Match those by code
        prefix as a fallback.
        """
        if super().handles(item, item_routing):
            return True
        if item.check_type == "standard" and item.code.upper().startswith(_HN_PREFIX):
            return True
        return False


# Self-register at import. The orchestrator (bvw.7) looks up agents by
# AGENT_ROUTING via app.agents.routing.get_agent_for().
register_agent(SanitaryAgent())
