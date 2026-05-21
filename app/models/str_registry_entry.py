"""Pydantic model for entries in data_pipeline/str_registry.json.

Used by app.services.str_registry_loader (and downstream by python-be-e6n.5
manual_import + python-be-bvw.7 orchestrator routing).
"""

from typing import Literal

from pydantic import BaseModel, Field

# Keep these literals tight so a typo in the JSON fails loudly at load time.
Area = Literal[
    "structural",
    "fire_safety",
    "sanitary",
    "energy",
    "documents",
    "general",
]

AgentRouting = Literal[
    "general",
    "fire_safety",
    "sanitary",
    "energy",
    "structural",
    "engineering_systems",
]

Category = Literal[
    "administrative",
    "essential_requirements",
    "performance",
    "buildings",
    "structures",
    "engineering_systems",
]


class STRRegistryEntry(BaseModel):
    """Master metadata for a single STR regulation.

    Mirrors one entry in data_pipeline/str_registry.json -> regulations[].
    """

    code: str = Field(..., description="Exact STR code, e.g. 'STR 2.02.01:2004'.")
    title: str = Field(..., description="Lithuanian title from the official act.")
    category: Category = Field(..., description="Top-level grouping in compliance.config.json.")
    area: Area = Field(..., description="Coarse domain tag for orchestrator grouping.")
    etar_url: str | None = Field(
        default=None,
        description="e-TAR URL or null when the act UUID is not yet known.",
    )
    etar_search_query: str = Field(
        ...,
        description="Free-text query suitable for e-TAR search (used by the scraper fallback).",
    )
    last_amended_year: int = Field(
        ...,
        description=(
            "Year encoded in the STR code. Note: this is the publication year, "
            "NOT necessarily the latest amendment date — fetch the real date from e-TAR."
        ),
    )
    filename: str = Field(..., description="Kebab-cased PDF filename used for downloaded artifacts.")
    agent_routing: AgentRouting = Field(
        ...,
        description="Stage-3 specialized agent that owns this regulation.",
    )


class STRRegistryFile(BaseModel):
    """Top-level shape of data_pipeline/str_registry.json."""

    version: int
    generated_for: str
    notes: list[str]
    regulations: list[STRRegistryEntry]
