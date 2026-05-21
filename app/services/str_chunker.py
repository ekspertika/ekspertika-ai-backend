"""STR-document chunker: word-boundary chunks with str_code/page/article metadata.

Sibling to ``app/services/chunker.py`` (project-document chunker). The project chunker
emits lean ``TextChunk``s with only page tracking; STR ingestion needs richer metadata
so the retriever can filter Chroma by ``str_code`` and surface article numbers in
citations. Defaults: 800 words / 100 overlap (slightly more overlap than the project
chunker since STR articles routinely span multiple chunks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Article-heading pattern. Matches things like "4.", "4.3.", "4.3.1." at the start of a
# token (most STR PDFs render headings flush-left, so this is good enough on extracted
# text). We intentionally cap at 4 segments — anything deeper is almost certainly a list
# item rather than a real article number.
_ARTICLE_RE = re.compile(r"^(\d+(?:\.\d+){0,3})\.?$")


@dataclass
class STRChunk:
    str_code: str            # e.g. "STR 2.02.01:2004"
    chunk_index: int         # 0-based within the STR doc
    text: str
    start_page: int          # 1-based
    end_page: int
    article: str | None      # e.g. "4.3" — best effort, None if not detected


def _tag_words_with_pages(pages: dict[int, str]) -> list[tuple[str, int]]:
    """Flatten {page_num: text} into a list of (word, page_num) tuples in reading order."""
    tagged: list[tuple[str, int]] = []
    for page_num in sorted(pages.keys()):
        for word in pages[page_num].split():
            tagged.append((word, page_num))
    return tagged


def _extract_article_at_or_before(
    tagged: list[tuple[str, int]], start_idx: int
) -> str | None:
    """Return the most recent article number visible at or before ``start_idx``.

    Best effort: scans backwards (and includes the start position) for the first token
    that matches the article-heading pattern. Used so each chunk knows which article
    it begins inside.
    """
    for i in range(start_idx, -1, -1):
        word = tagged[i][0]
        match = _ARTICLE_RE.match(word)
        if match:
            return match.group(1)
    return None


def chunk_str_pdf(
    str_code: str,
    pages: dict[int, str],
    chunk_words: int = 800,
    overlap_words: int = 100,
) -> list[STRChunk]:
    """Chunk an STR document. ``pages`` is the {page_num: text} dict from PDFExtractor.

    Each chunk records the most recently-seen article heading, so retrieval results can
    cite "STR 2.02.01:2004, art. 4.3, p. 12". If no heading is visible, ``article`` is
    None — that's fine, metadata is bonus, not required for retrieval.
    """
    if chunk_words <= 0:
        raise ValueError("chunk_words must be positive")
    if overlap_words < 0 or overlap_words >= chunk_words:
        raise ValueError("overlap_words must be in [0, chunk_words)")

    tagged = _tag_words_with_pages(pages)
    if not tagged:
        return []

    chunks: list[STRChunk] = []
    step = chunk_words - overlap_words
    i = 0
    index = 0
    while i < len(tagged):
        slice_ = tagged[i : i + chunk_words]
        chunks.append(
            STRChunk(
                str_code=str_code,
                chunk_index=index,
                text=" ".join(w for w, _ in slice_),
                start_page=slice_[0][1],
                end_page=slice_[-1][1],
                article=_extract_article_at_or_before(tagged, i),
            )
        )
        i += step
        index += 1
    return chunks
