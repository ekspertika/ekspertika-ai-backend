"""Download representative STR PDFs from e-tar.lt for the Ekspertika RAG pipeline.

Beads issue: python-be-e6n.1 (manual download, representative subset).
Full 30-STR scrape lives in python-be-e6n.9 (auto-scraper).

Why this exists
---------------
The Stage-2 RAG ingest (python-be-e6n.5) needs *real* Lithuanian STR text to
validate the chunker + embedder + retriever end-to-end. Five to eight PDFs
spanning all `agent_routing` groups (structural / fire_safety / energy /
engineering_systems / sanitary / general) is enough to unblock that work.

Strategy
--------
e-tar.lt's search form is a JSF/PrimeFaces server-side abomination — POSTing
the right `j_id_*` fields is brittle and likely to break on every redeploy.
Until the scraper bead (e6n.9) tackles real search, this script ships with a
curated `KNOWN_DOCUMENT_IDS` map gathered manually (Google "site:e-tar.lt
<code>"). For each target STR we:

1. Fetch the act page `/portal/lt/legalAct/<doc_id>` to confirm it exists.
2. Try the consolidated edition (`/asr`) — that's the *current* legal text
   including amendments. Extract its `actualedition/<base>/<edition>/format/
   ISO_PDF/` link.
3. Fall back to the original-edition PDF: `/rs/legalact/<doc_id>/format/
   ISO_PDF/`.
4. If both PDF paths fail, save the act HTML page as a last resort.

Polite: 1.0 s sleep between requests, real User-Agent header, idempotent
(skip if target file already exists).

Run
---
    uv sync --extra scraping
    uv run python scripts/download_str.py            # the 5 must-haves
    uv run python scripts/download_str.py --codes "STR 2.02.01:2004,HN 42:2009"
    uv run python scripts/download_str.py --all-known   # all 8 known IDs

Acceptance: at least 5 PDFs (or HTML fallbacks) under data/str_texts/, with
their `etar_url` populated in data_pipeline/str_registry.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import httpx
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover - guidance for fresh envs
    print(
        "Missing scraping deps. Install with:\n"
        "    uv sync --extra scraping\n"
        f"Original error: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

# --- paths ------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "data_pipeline" / "str_registry.json"
OUTPUT_DIR = REPO_ROOT / "data" / "str_texts"

# --- known document IDs ----------------------------------------------------
# Curated 2026-04 by pythondev-3 (e6n.1) via Google "site:e-tar.lt <code>".
# Each value is the e-tar `documentId` segment that follows /portal/lt/legalAct/.
# Two ID schemes coexist on e-tar:
#   * Legacy "TAR.<HEX>" (older acts imported from TAIS).
#   * UUID-like 32-hex (acts published natively in e-tar since ~2014).
# Both work as input to /rs/legalact/<id>/format/ISO_PDF/.
KNOWN_DOCUMENT_IDS: dict[str, str] = {
    # Must-haves (representative across agent_routing groups)
    "STR 2.02.01:2004": "TAR.20B8999D0CC7",  # structural — Gyvenamieji pastatai
    "STR 2.05.05:2005": "TAR.C8C4EF7FF7AE",  # structural — Betoninės/gelžbetoninės
    "STR 2.01.01(2):1999": "TAR.6CA64A9DFF4C",  # fire_safety — Gaisrinė sauga
    "STR 2.01.02:2016": "2c182f10b6bf11e6aae49c0b9525cbbb",  # energy — Energinis naudingumas
    "STR 2.04.01:2018": "1aa5acc055ce11e9975f9c35aedfe438",  # energy — Pastatų atitvaros
    # Stretch goals
    "STR 1.04.04:2017": "ad75ac40a7dd11e69ad4c8713b612d0f",  # general — Projektavimas, ekspertizė
    "HN 42:2009": "TAR.480FD840BA61",  # sanitary — Gyvenamųjų patalpų mikroklimatas
    "STR 2.03.01:2019": "103022d0ffbe11e99681cd81dcdca52c",  # general — Statinių prieinamumas
}

DEFAULT_TARGET_CODES: tuple[str, ...] = (
    "STR 2.02.01:2004",
    "STR 2.05.05:2005",
    "STR 2.01.01(2):1999",
    "STR 2.01.02:2016",
    "STR 2.04.01:2018",
)

# --- HTTP -------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
SLEEP_SECONDS = 1.0
TIMEOUT_SECONDS = 60.0
PDF_MIN_BYTES = 50_000  # 50 KB — guard against truncated/error PDFs
HTML_MIN_BYTES = 20_000


# --- data types -------------------------------------------------------------


@dataclass
class DownloadResult:
    code: str
    filename: str
    status: str  # "downloaded" | "skipped" | "failed"
    method: str | None = None  # "pdf" | "html"
    etar_url: str | None = None
    bytes_written: int = 0
    note: str = ""


@dataclass
class Reporter:
    rows: list[DownloadResult] = field(default_factory=list)

    def add(self, r: DownloadResult) -> None:
        self.rows.append(r)
        flag = {"downloaded": "OK", "skipped": "..", "failed": "!!"}.get(r.status, "??")
        method = f"[{r.method}]" if r.method else ""
        size = f"{r.bytes_written // 1024} KB" if r.bytes_written else ""
        print(f"  {flag} {r.code:<22} {method:<7} {size:<10} {r.note}")

    def summary(self) -> None:
        print("\n=== Summary ===")
        ok = [r for r in self.rows if r.status in ("downloaded", "skipped")]
        fail = [r for r in self.rows if r.status == "failed"]
        print(f"  downloaded/skipped: {len(ok)} / {len(self.rows)}")
        for r in self.rows:
            tag = {"downloaded": "OK", "skipped": "SKIP", "failed": "FAIL"}[r.status]
            print(f"    [{tag}] {r.code} -> {r.filename} ({r.method or '-'})  {r.note}")
        if fail:
            print(f"  failures: {len(fail)}")


# --- helpers ----------------------------------------------------------------


def _polite_sleep() -> None:
    time.sleep(SLEEP_SECONDS)


def _is_pdf_bytes(b: bytes) -> bool:
    return b.startswith(b"%PDF-")


def _make_client() -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "application/pdf,*/*;q=0.8",
            "Accept-Language": "lt,en;q=0.9",
        },
        follow_redirects=True,
        timeout=TIMEOUT_SECONDS,
    )


def _resolve_consolidated_pdf_url(client: httpx.Client, document_id: str) -> str | None:
    """Try /asr to find the consolidated-edition ISO_PDF link.

    Returns absolute URL or None if no consolidated edition exists.
    """
    asr_url = f"https://www.e-tar.lt/portal/lt/legalAct/{document_id}/asr"
    print(f"    GET {asr_url}")
    resp = client.get(asr_url)
    _polite_sleep()
    if resp.status_code != 200:
        print(f"    /asr returned HTTP {resp.status_code}, skipping consolidated path")
        return None
    soup = BeautifulSoup(resp.text, "lxml")
    # Look for /rs/actualedition/<base>/<edition>/format/ISO_PDF/
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/rs/actualedition/" in href and "format/ISO_PDF" in href:
            if href.startswith("/"):
                href = "https://www.e-tar.lt" + href
            return href
    return None


def _download_pdf(client: httpx.Client, url: str, dest: Path) -> tuple[bool, int, str]:
    """Download a URL to dest. Returns (ok, bytes_written, note)."""
    print(f"    GET {url}")
    resp = client.get(url)
    _polite_sleep()
    if resp.status_code != 200:
        return False, 0, f"HTTP {resp.status_code}"
    body = resp.content
    if not _is_pdf_bytes(body):
        return False, 0, f"not a PDF (got {len(body)} bytes, magic={body[:6]!r})"
    if len(body) < PDF_MIN_BYTES:
        return False, 0, f"PDF too small ({len(body)} < {PDF_MIN_BYTES})"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return True, len(body), f"saved {len(body) // 1024} KB"


def _download_html(client: httpx.Client, url: str, dest: Path) -> tuple[bool, int, str]:
    print(f"    GET {url} (HTML fallback)")
    resp = client.get(url)
    _polite_sleep()
    if resp.status_code != 200:
        return False, 0, f"HTTP {resp.status_code}"
    body = resp.content
    if len(body) < HTML_MIN_BYTES:
        return False, 0, f"HTML too small ({len(body)} < {HTML_MIN_BYTES})"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return True, len(body), f"saved {len(body) // 1024} KB (HTML)"


def _act_page_url(document_id: str) -> str:
    return f"https://www.e-tar.lt/portal/lt/legalAct/{document_id}"


def _original_pdf_url(document_id: str) -> str:
    return f"https://www.e-tar.lt/rs/legalact/{document_id}/format/ISO_PDF/"


def _html_dest(filename: str) -> str:
    # Swap .pdf for .html for the HTML fallback.
    return re.sub(r"\.pdf$", ".html", filename, flags=re.IGNORECASE)


# --- core flow --------------------------------------------------------------


def download_one(
    client: httpx.Client,
    code: str,
    filename: str,
    document_id: str,
) -> DownloadResult:
    pdf_path = OUTPUT_DIR / filename
    html_path = OUTPUT_DIR / _html_dest(filename)

    if pdf_path.exists() and pdf_path.stat().st_size >= PDF_MIN_BYTES:
        return DownloadResult(
            code=code,
            filename=filename,
            status="skipped",
            method="pdf",
            etar_url=_act_page_url(document_id),
            bytes_written=pdf_path.stat().st_size,
            note="already present",
        )
    if html_path.exists() and html_path.stat().st_size >= HTML_MIN_BYTES:
        return DownloadResult(
            code=code,
            filename=html_path.name,
            status="skipped",
            method="html",
            etar_url=_act_page_url(document_id),
            bytes_written=html_path.stat().st_size,
            note="already present (html fallback)",
        )

    print(f"\n[{code}] document_id={document_id}")

    # Strategy 1: consolidated edition PDF (preferred — current legal text)
    consolidated_pdf = _resolve_consolidated_pdf_url(client, document_id)
    if consolidated_pdf:
        ok, n, note = _download_pdf(client, consolidated_pdf, pdf_path)
        if ok:
            return DownloadResult(
                code=code,
                filename=filename,
                status="downloaded",
                method="pdf",
                etar_url=_act_page_url(document_id),
                bytes_written=n,
                note=f"consolidated; {note}",
            )
        print(f"    consolidated PDF failed: {note}")

    # Strategy 2: original-edition PDF
    ok, n, note = _download_pdf(client, _original_pdf_url(document_id), pdf_path)
    if ok:
        return DownloadResult(
            code=code,
            filename=filename,
            status="downloaded",
            method="pdf",
            etar_url=_act_page_url(document_id),
            bytes_written=n,
            note=f"original edition; {note}",
        )
    print(f"    original PDF failed: {note}")

    # Strategy 3: HTML fallback (act page itself)
    ok, n, note_html = _download_html(client, _act_page_url(document_id), html_path)
    if ok:
        return DownloadResult(
            code=code,
            filename=html_path.name,
            status="downloaded",
            method="html",
            etar_url=_act_page_url(document_id),
            bytes_written=n,
            note=f"HTML fallback; {note_html}",
        )

    return DownloadResult(
        code=code,
        filename=filename,
        status="failed",
        method=None,
        etar_url=None,
        note=f"all strategies failed (PDF: {note}; HTML: {note_html})",
    )


def update_registry(results: Iterable[DownloadResult]) -> int:
    """Patch etar_url + download_method onto matching rows. Returns # updated."""
    by_code = {r.code: r for r in results if r.status in ("downloaded", "skipped")}
    if not by_code:
        return 0
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    updated = 0
    for reg in data["regulations"]:
        r = by_code.get(reg["code"])
        if not r or not r.etar_url:
            continue
        if reg.get("etar_url") != r.etar_url or reg.get("download_method") != r.method:
            reg["etar_url"] = r.etar_url
            reg["download_method"] = r.method
            updated += 1
    if updated:
        REGISTRY_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return updated


# --- CLI --------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download STR PDFs from e-tar.lt.")
    p.add_argument(
        "--codes",
        type=str,
        default=None,
        help="Comma-separated STR codes. Default = the 5 representative must-haves.",
    )
    p.add_argument(
        "--all-known",
        action="store_true",
        help="Download every code in KNOWN_DOCUMENT_IDS (~8 acts).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.all_known:
        target_codes = list(KNOWN_DOCUMENT_IDS.keys())
    elif args.codes:
        target_codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        target_codes = list(DEFAULT_TARGET_CODES)

    if not REGISTRY_PATH.exists():
        print(f"ERROR: registry not found at {REGISTRY_PATH}", file=sys.stderr)
        return 1

    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    by_code = {r["code"]: r for r in registry["regulations"]}

    print(f"Output dir: {OUTPUT_DIR}")
    print(f"Target codes ({len(target_codes)}): {target_codes}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    reporter = Reporter()
    with _make_client() as client:
        for code in target_codes:
            entry = by_code.get(code)
            if not entry:
                reporter.add(
                    DownloadResult(
                        code=code,
                        filename="?",
                        status="failed",
                        note="not in str_registry.json",
                    )
                )
                continue
            doc_id = KNOWN_DOCUMENT_IDS.get(code)
            if not doc_id:
                reporter.add(
                    DownloadResult(
                        code=code,
                        filename=entry["filename"],
                        status="failed",
                        note="no known document_id (extend KNOWN_DOCUMENT_IDS)",
                    )
                )
                continue
            try:
                result = download_one(client, code, entry["filename"], doc_id)
            except httpx.HTTPError as e:
                result = DownloadResult(
                    code=code,
                    filename=entry["filename"],
                    status="failed",
                    note=f"httpx error: {e!r}",
                )
            reporter.add(result)

    n_updated = update_registry(reporter.rows)
    print(f"\nRegistry updated: {n_updated} rows -> {REGISTRY_PATH}")
    reporter.summary()

    n_ok = sum(1 for r in reporter.rows if r.status in ("downloaded", "skipped"))
    return 0 if n_ok >= 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
