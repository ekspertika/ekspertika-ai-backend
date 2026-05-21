"""Agent registry — maps ``agent_routing`` strings to concrete agent instances.

Concrete specialized agents (bvw.2..bvw.6) call ``register_agent`` at module
import time, so the orchestrator (bvw.7) can look them up without hard-coding
imports of every agent module.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.base_agent import BaseAgent

# Filled in as concrete agents land via bvw.2..bvw.6. The orchestrator uses
# this to look up which agent handles a given agent_routing value.
AGENT_REGISTRY: dict[str, "BaseAgent"] = {}


def register_agent(agent: "BaseAgent") -> None:
    """Register a specialized agent.

    Called once at module import time by each concrete agent's module — keeps
    the orchestrator decoupled from individual agent imports.
    """
    AGENT_REGISTRY[agent.AGENT_ROUTING] = agent


def get_agent_for(routing: str) -> "BaseAgent | None":
    """Return the registered agent for ``routing``, or ``None`` if no match."""
    return AGENT_REGISTRY.get(routing)
