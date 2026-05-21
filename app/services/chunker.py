"""Word-boundary chunking with page tracking. Port of nextjs-fe/lib/pdf/chunker.ts."""

from dataclasses import dataclass

CHUNK_WORDS = 800
OVERLAP_WORDS = 80


@dataclass
class TextChunk:
    index: int
    text: str
    start_page: int
    end_page: int


def chunk_pages(pages: dict[int, str]) -> list[TextChunk]:
    """Build overlapping word chunks from a {page_num: text} dict.

    Each chunk records its start_page and end_page so source_pages can flow through to the
    final result. Pages with empty text contribute nothing.
    """
    tagged: list[tuple[str, int]] = []
    for page_num in sorted(pages.keys()):
        for word in pages[page_num].split():
            tagged.append((word, page_num))

    chunks: list[TextChunk] = []
    i = 0
    index = 0
    while i < len(tagged):
        slice_ = tagged[i : i + CHUNK_WORDS]
        chunks.append(
            TextChunk(
                index=index,
                text=" ".join(w for w, _ in slice_),
                start_page=slice_[0][1],
                end_page=slice_[-1][1],
            )
        )
        i += CHUNK_WORDS - OVERLAP_WORDS
        index += 1
    return chunks


def score_chunk_relevance(chunk: TextChunk, terms: list[str]) -> int:
    lower = chunk.text.lower()
    return sum(lower.count(t.lower()) for t in terms)


def select_top_chunks(
    chunks: list[TextChunk], terms: list[str], top_n: int = 3
) -> list[TextChunk]:
    """Top-N relevance-scored chunks, returned in original document order."""
    if len(chunks) <= top_n:
        return chunks
    scored = sorted(
        ((c, score_chunk_relevance(c, terms)) for c in chunks), key=lambda x: -x[1]
    )[:top_n]
    return sorted((c for c, _ in scored), key=lambda c: c.index)
