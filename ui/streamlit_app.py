"""Streamlit web UI for the STR Document Compliance Checker."""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on sys.path (needed when Streamlit runs from ui/ dir)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from app.handlers.check_handler import CheckHandler
from app.models.check_result import ComplianceReport
from config.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

st.set_page_config(
    page_title="STR Dokumentų tikrintuvas",
    page_icon="🏗️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

STATUS_EMOJI = {
    "pass": "✅",
    "fail": "❌",
    "partial": "⚠️",
    "not_applicable": "—",
    "error": "🔴",
}

STATUS_COLORS = {
    "pass": "#C6EFCE",
    "fail": "#FFC7CE",
    "partial": "#FFEB9C",
    "not_applicable": "#F2F2F2",
    "error": "#FFC7CE",
}

STATUS_LABELS = {
    "pass": "Atitinka",
    "fail": "Neatitinka",
    "partial": "Dalinai atitinka",
    "not_applicable": "Netaikoma",
    "error": "Klaida",
}


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _metric_card(label: str, value: int, color: str = "#1F4E79") -> None:
    st.markdown(
        f"""
        <div style="background:{color};border-radius:8px;padding:16px;text-align:center;color:white;">
            <div style="font-size:2rem;font-weight:bold;">{value}</div>
            <div style="font-size:0.85rem;opacity:0.9;">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _show_summary(report: ComplianceReport) -> None:
    st.subheader("Suvestinė")

    cols = st.columns(5)
    with cols[0]:
        _metric_card("Patikrinta normatyvų", len(report.str_results), "#1F4E79")
    with cols[1]:
        _metric_card("✅ Atitinka", report.pass_count, "#375623")
    with cols[2]:
        _metric_card("❌ Neatitinka", report.fail_count, "#9C0006")
    with cols[3]:
        _metric_card("⚠️ Dalinai", report.partial_count, "#7D6608")
    with cols[4]:
        docs_found = report.docs_found_count
        docs_total = len(report.document_results)
        color = "#375623" if docs_found == docs_total else "#9C0006"
        _metric_card(f"Dokumentai {docs_found}/{docs_total}", docs_total - docs_found, color)

    if report.warnings:
        for warning in report.warnings:
            st.warning(warning)


def _show_str_results(report: ComplianceReport) -> None:
    st.subheader("STR atitikties rezultatai")

    categories = sorted({r.category for r in report.str_results})
    cat_filter = st.multiselect(
        "Filtruoti pagal kategoriją",
        options=categories,
        default=categories,
        format_func=lambda c: {"str": "STR", "law": "Įstatymas", "hn": "HN norma", "other": "Kita"}.get(
            c, c
        ),
    )
    status_filter = st.multiselect(
        "Filtruoti pagal statusą",
        options=list(STATUS_LABELS.keys()),
        default=list(STATUS_LABELS.keys()),
        format_func=lambda s: STATUS_LABELS.get(s, s),
    )

    filtered = [
        r for r in report.str_results if r.category in cat_filter and r.status in status_filter
    ]

    if not filtered:
        st.info("Nėra rezultatų pagal pasirinktus filtrus.")
        return

    for result in filtered:
        emoji = STATUS_EMOJI.get(result.status, "?")
        bg = STATUS_COLORS.get(result.status, "#FFFFFF")
        label = STATUS_LABELS.get(result.status, result.status)
        pages = ", ".join(str(p) for p in sorted(result.page_references)) if result.page_references else "—"
        confidence_label = {"high": "Aukštas", "medium": "Vidutinis", "low": "Žemas"}.get(
            result.confidence, result.confidence
        )

        with st.expander(f"{emoji} {result.code} — {result.full_name[:60]}"):
            st.markdown(
                f"""
                <div style="background:{bg};border-radius:6px;padding:10px;margin-bottom:8px;">
                    <b>Statusas:</b> {label} &nbsp;|&nbsp;
                    <b>Patikimumas:</b> {confidence_label} &nbsp;|&nbsp;
                    <b>Minimas puslapiuose:</b> {pages}
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown(f"**Komentaras:** {result.comment}")
            if result.error_message:
                st.error(f"Klaida: {result.error_message}")


def _show_doc_results(report: ComplianceReport) -> None:
    st.subheader("Privalomieji dokumentai")

    for result in report.document_results:
        icon = "✅" if result.found else "❌"
        color = "green" if result.found else "red"
        with st.expander(f"{icon} {result.document_name}"):
            st.markdown(result.comment)


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------


def main() -> None:
    st.title("🏗️ STR Dokumentų tikrintuvas")
    st.markdown(
        "Įkelkite statybos projekto dokumentą PDF formatu. "
        "Sistema patikrins jo atitiktį STR reglamentams, HN normoms ir kitiems teisės aktams."
    )

    if not Config.validate():
        st.error(
            "⚠️ **OPENAI_API_KEY** nenurodytas. "
            "Sukurkite `.env` failą pagal `.env.example` šabloną ir paleiskite programą iš naujo."
        )
        return

    uploaded_file = st.file_uploader(
        "Pasirinkite PDF dokumentą",
        type=["pdf"],
        help="Maksimalus failo dydis: 200 MB. Dokumentas turi turėti teksto sluoksnį (ne skenų).",
    )

    if uploaded_file is None:
        st.info("Įkelkite PDF dokumentą, kad pradėtumėte tikrinimą.")
        return

    st.success(f"Failas įkeltas: **{uploaded_file.name}** ({uploaded_file.size / 1024:.0f} KB)")

    if st.button("🔍 Tikrinti dokumentą", type="primary", use_container_width=True):
        progress_placeholder = st.empty()
        status_box = st.status("Vykdomas tikrinimas...", expanded=True)

        def on_progress(msg: str) -> None:
            status_box.write(msg)

        try:
            handler = CheckHandler()
            report, excel_bytes = handler.check_from_bytes(
                pdf_bytes=uploaded_file.read(),
                filename=uploaded_file.name,
                on_progress=on_progress,
            )
            status_box.update(label="Tikrinimas baigtas!", state="complete", expanded=False)
        except Exception as exc:
            status_box.update(label="Klaida!", state="error")
            st.error(f"Tikrinimas nepavyko: {exc}")
            return

        st.divider()
        _show_summary(report)

        st.divider()
        tab_str, tab_docs = st.tabs(["📋 STR normatyvai", "📂 Privalomieji dokumentai"])

        with tab_str:
            _show_str_results(report)

        with tab_docs:
            _show_doc_results(report)

        st.divider()
        report_name = f"ataskaita_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        st.download_button(
            label="⬇️ Atsisiųsti Excel ataskaitą",
            data=excel_bytes,
            file_name=report_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
