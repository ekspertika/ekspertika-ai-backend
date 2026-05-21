"""Load data_pipeline/str_registry.json into typed STRRegistryEntry models.

Mirrors the style of app.services.config_loader: module-level cache, simple
list-returning loader, and a code-based lookup helper.

Consumed by:
- python-be-e6n.5 (manual_import) — which PDFs to ingest, where to store them
- python-be-bvw.7 (orchestrator) — agent_routing for Stage-3 multi-agent work
"""

import json
from pathlib import Path

from app.models.str_registry_entry import STRRegistryEntry, STRRegistryFile

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "data_pipeline" / "str_registry.json"
_cached: list[STRRegistryEntry] | None = None
_by_code: dict[str, STRRegistryEntry] | None = None


def load_str_registry() -> list[STRRegistryEntry]:
    """Read and validate data_pipeline/str_registry.json. Result is cached."""
    global _cached, _by_code
    if _cached is not None:
        return _cached

    raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    parsed = STRRegistryFile.model_validate(raw)
    _cached = parsed.regulations
    _by_code = {entry.code: entry for entry in _cached}
    return _cached


def get_by_code(code: str) -> STRRegistryEntry | None:
    """Return the registry entry whose `code` matches exactly, or None."""
    if _by_code is None:
        load_str_registry()
    assert _by_code is not None  # for type checkers
    return _by_code.get(code)
