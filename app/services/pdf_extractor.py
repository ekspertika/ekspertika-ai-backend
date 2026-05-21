import logging
import os
import re
import tempfile

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

SCAN_THRESHOLD_CHARS_PER_PAGE = 100


class ExtractedPDF:
    def __init__(
        self,
        filename: str,
        page_count: int,
        pages: dict[int, str],
        is_scanned: bool,
        warnings: list[str],
    ) -> None:
        self.filename = filename
        self.page_count = page_count
        self.pages = pages  # 1-indexed: {1: "text", 2: "text", ...}
        self.is_scanned = is_scanned
        self.warnings = warnings

    @property
    def total_chars(self) -> int:
        return sum(len(t) for t in self.pages.values())

    @property
    def full_text(self) -> str:
        parts = [f"[Puslapis {num}]\n{text}" for num, text in sorted(self.pages.items())]
        return "\n\n".join(parts)

    def get_pages_mentioning(self, code: str) -> dict[int, str]:
        """Return pages that contain the given normative code."""
        # Flexible pattern: allow variable whitespace between code parts
        escaped = re.escape(code)
        pattern = re.compile(escaped.replace(r"\ ", r"\\s*"), re.IGNORECASE)
        return {num: text for num, text in self.pages.items() if pattern.search(text)}

    def get_context_window(self, page_numbers: list[int], context: int = 1) -> str:
        """Return text for given pages plus surrounding context pages."""
        expanded: set[int] = set()
        for p in page_numbers:
            for cp in range(max(1, p - context), min(self.page_count + 1, p + context + 1)):
                expanded.add(cp)

        parts = []
        for num in sorted(expanded):
            if num in self.pages:
                parts.append(f"[Puslapis {num}]\n{self.pages[num]}")
        return "\n\n".join(parts)


class PDFExtractor:
    def extract(self, pdf_path: str) -> ExtractedPDF:
        """Extract text from a PDF file on disk."""
        logger.info("Extracting text from: %s", pdf_path)
        warnings: list[str] = []

        try:
            doc = fitz.open(pdf_path)
        except Exception as exc:
            raise ValueError(f"Cannot open PDF: {exc}") from exc

        pages: dict[int, str] = {}
        for idx in range(len(doc)):
            page = doc[idx]
            text = page.get_text("text")
            pages[idx + 1] = text

        doc.close()

        avg_chars = sum(len(t) for t in pages.values()) / max(len(pages), 1)
        is_scanned = avg_chars < SCAN_THRESHOLD_CHARS_PER_PAGE

        if is_scanned:
            msg = (
                "Dokumentas atrodo kaip nuskenuotas (mažai teksto puslapyje). "
                "Automatinis tikrinimas gali būti netikslius. "
                "Rekomenduojame naudoti dokumentą su teksto sluoksniu."
            )
            warnings.append(msg)
            logger.warning("Low text density (%.0f chars/page) — possible scanned PDF", avg_chars)
        else:
            logger.info(
                "Extracted %d pages, %.0f chars/page avg",
                len(pages),
                avg_chars,
            )

        return ExtractedPDF(
            filename=os.path.basename(pdf_path),
            page_count=len(pages),
            pages=pages,
            is_scanned=is_scanned,
            warnings=warnings,
        )

    def extract_from_bytes(self, pdf_bytes: bytes, filename: str = "document.pdf") -> ExtractedPDF:
        """Extract text from raw PDF bytes (e.g. from Streamlit file uploader)."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            result = self.extract(tmp_path)
            result.filename = filename
            return result
        finally:
            os.unlink(tmp_path)
