import asyncio
import logging
from collections.abc import Callable

from app.models.check_result import ComplianceReport
from app.services.excel_reporter import ExcelReporter
from app.services.pdf_extractor import PDFExtractor
from app.services.str_checker import STRChecker
from config.config import Config

logger = logging.getLogger(__name__)


class CheckHandler:
    """Orchestrates PDF extraction → STR compliance check → Excel report generation."""

    def __init__(self) -> None:
        self.extractor = PDFExtractor()
        self.checker = STRChecker()
        self.reporter = ExcelReporter()

    def check_from_bytes(
        self,
        pdf_bytes: bytes,
        filename: str = "document.pdf",
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[ComplianceReport, bytes]:
        """Synchronous entry point for Streamlit (runs async code in event loop)."""
        return asyncio.run(self._run(pdf_bytes, filename, on_progress))

    async def check_from_bytes_async(
        self,
        pdf_bytes: bytes,
        filename: str = "document.pdf",
        on_progress: Callable[[str], None] | None = None,
    ) -> tuple[ComplianceReport, bytes]:
        """Async entry point for CLI or future async callers."""
        return await self._run(pdf_bytes, filename, on_progress)

    async def _run(
        self,
        pdf_bytes: bytes,
        filename: str,
        on_progress: Callable[[str], None] | None,
    ) -> tuple[ComplianceReport, bytes]:
        if not Config.validate():
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Please create a .env file based on .env.example."
            )

        def progress(msg: str) -> None:
            logger.info(msg)
            if on_progress:
                on_progress(msg)

        progress("Skaitomas PDF dokumentas...")
        pdf = self.extractor.extract_from_bytes(pdf_bytes, filename)
        progress(f"PDF nuskaitytas: {pdf.page_count} puslapiai, {pdf.total_chars:,} simboliai.")

        report = await self.checker.check(pdf, on_progress=on_progress)

        progress("Generuojama Excel ataskaita...")
        excel_bytes = self.reporter.generate(report)

        return report, excel_bytes
