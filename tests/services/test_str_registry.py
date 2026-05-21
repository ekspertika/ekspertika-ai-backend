"""Consistency checks between compliance.config.json and data_pipeline/str_registry.json."""

import json
from pathlib import Path

from app.services.str_registry_loader import get_by_code, load_str_registry

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPLIANCE_CONFIG = _REPO_ROOT / "compliance.config.json"


def _all_str_codes_from_compliance_config() -> list[str]:
    raw = json.loads(_COMPLIANCE_CONFIG.read_text(encoding="utf-8"))
    return [code for codes in raw["str"].values() for code in codes]


def test_every_str_code_has_registry_entry():
    expected = _all_str_codes_from_compliance_config()
    registry = load_str_registry()
    registry_codes = {e.code for e in registry}

    missing = [code for code in expected if code not in registry_codes]
    assert not missing, f"Missing registry entries for: {missing}"


def test_no_extra_registry_entries():
    expected = set(_all_str_codes_from_compliance_config())
    registry = load_str_registry()
    extras = [e.code for e in registry if e.code not in expected]
    assert not extras, f"Registry has codes not present in compliance.config.json: {extras}"


def test_etar_url_is_null_or_etar_domain():
    for entry in load_str_registry():
        if entry.etar_url is not None:
            assert entry.etar_url.startswith("https://www.e-tar.lt/"), (
                f"{entry.code} has non-e-TAR URL: {entry.etar_url}"
            )


def test_get_by_code_roundtrip():
    for entry in load_str_registry():
        assert get_by_code(entry.code) is entry


def test_filename_is_kebab_pdf():
    for entry in load_str_registry():
        assert entry.filename.endswith(".pdf"), entry.filename
        assert entry.filename == entry.filename.lower(), entry.filename
        assert " " not in entry.filename and ":" not in entry.filename, entry.filename
