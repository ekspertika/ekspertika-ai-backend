"""Tests for app.services.str_chunker."""

from __future__ import annotations

from app.services.str_chunker import STRChunk, chunk_str_pdf


def _make_pages(words_per_page: int, page_count: int, prefix: str = "word") -> dict[int, str]:
    """Build a synthetic {page_num: text} dict with predictable, page-traceable tokens."""
    return {
        p: " ".join(f"{prefix}{p}_{i}" for i in range(words_per_page))
        for p in range(1, page_count + 1)
    }


class TestChunkStrPdf:
    def test_three_page_doc_chunk_count_and_page_range(self) -> None:
        # 3 pages * 600 words = 1800 words; with chunk=800/overlap=100, step=700.
        # Starts: 0, 700, 1400 → 3 chunks. Last chunk has 1800-1400=400 words.
        pages = _make_pages(words_per_page=600, page_count=3)
        chunks = chunk_str_pdf("STR 2.02.01:2004", pages)

        assert len(chunks) == 3
        assert all(isinstance(c, STRChunk) for c in chunks)

        # First chunk starts on page 1; words 0-799 span pages 1 (0-599) and 2 (600-1199).
        assert chunks[0].start_page == 1
        assert chunks[0].end_page == 2
        assert chunks[0].chunk_index == 0
        assert chunks[0].str_code == "STR 2.02.01:2004"

        # Second chunk starts at word 700 (page 2), ends at word 1499 (page 3).
        assert chunks[1].start_page == 2
        assert chunks[1].end_page == 3
        assert chunks[1].chunk_index == 1

        # Third chunk starts at word 1400 (page 3), ends at the last word (page 3).
        assert chunks[2].start_page == 3
        assert chunks[2].end_page == 3
        assert chunks[2].chunk_index == 2

    def test_overlap_preserves_word_boundaries(self) -> None:
        """Overlap slice must reproduce intact words from the previous chunk — no splits."""
        pages = _make_pages(words_per_page=500, page_count=2)
        chunks = chunk_str_pdf("STR X", pages, chunk_words=300, overlap_words=50)

        assert len(chunks) >= 2
        first_words = chunks[0].text.split()
        second_words = chunks[1].text.split()

        # Step = 300-50 = 250. First chunk has words 0..299; second has 250..549.
        # The overlap is the last 50 words of chunk[0] (250..299) == first 50 of chunk[1].
        assert first_words[-50:] == second_words[:50]
        # And every overlap word should be a complete, untruncated synthetic token.
        for w in first_words[-50:]:
            assert w.startswith("word")
            assert "_" in w

    def test_article_extraction_at_chunk_start(self) -> None:
        """When a chunk begins after a '4.3.' heading, that article should be tagged."""
        # Page 1: 100 filler words, then "4.3." heading + body words.
        # We'll use chunk_words=100/overlap=0 so the second chunk starts exactly where
        # the heading appears.
        filler = " ".join(f"intro{i}" for i in range(100))
        body = "4.3. " + " ".join(f"body{i}" for i in range(150))
        pages = {1: f"{filler} {body}"}

        chunks = chunk_str_pdf("STR T", pages, chunk_words=100, overlap_words=0)

        # Chunk 0 = the 100 intro words (no article seen yet).
        # Chunk 1 starts at word 100, which is "4.3." → article should be "4.3".
        assert chunks[0].article is None
        assert chunks[1].article == "4.3"

        # And subsequent chunks (still inside article 4.3) should keep that label.
        assert chunks[2].article == "4.3"

    def test_empty_pages(self) -> None:
        assert chunk_str_pdf("STR EMPTY", {}) == []
        assert chunk_str_pdf("STR EMPTY", {1: "", 2: ""}) == []

    def test_single_short_page(self) -> None:
        pages = {1: "alpha beta gamma delta"}
        chunks = chunk_str_pdf("STR S", pages, chunk_words=800, overlap_words=100)
        assert len(chunks) == 1
        assert chunks[0].text == "alpha beta gamma delta"
        assert chunks[0].start_page == 1
        assert chunks[0].end_page == 1

    def test_article_carries_through_until_next_heading(self) -> None:
        text = (
            "1. " + " ".join(f"a{i}" for i in range(50))
            + " 2.1. " + " ".join(f"b{i}" for i in range(50))
            + " 2.2. " + " ".join(f"c{i}" for i in range(50))
        )
        pages = {1: text}
        chunks = chunk_str_pdf("STR A", pages, chunk_words=40, overlap_words=0)

        articles = [c.article for c in chunks]
        # First chunk starts at "1." → article "1".
        assert articles[0] == "1"
        # Somewhere later we should see article transition to "2.1" then "2.2".
        assert "2.1" in articles
        assert "2.2" in articles
