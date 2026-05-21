"""Unit tests for app.services.retriever.STRRetriever.

Both the vector store and the embedder are fully mocked — these tests stay
pure and don't require chromadb or an OpenAI key. STRVectorStore's real
Chroma integration is exercised in tests/services/test_vector_store.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.retriever import RetrievedChunk, STRRetriever


def _hit(
    *,
    str_code: str,
    text: str = "regulation text",
    chunk_index: int = 0,
    start_page: int = 1,
    end_page: int = 2,
    article: str | None = "4.3",
    distance: float = 0.2,
) -> dict:
    """Build a fake STRVectorStore.query() hit."""
    md: dict = {
        "str_code": str_code,
        "chunk_index": chunk_index,
        "start_page": start_page,
        "end_page": end_page,
    }
    if article is not None:
        md["article"] = article
    return {
        "id": f"{str_code}::{chunk_index}",
        "text": text,
        "metadata": md,
        "distance": distance,
    }


def _make_retriever(
    hits: list[dict],
    *,
    top_k: int = 5,
) -> tuple[STRRetriever, MagicMock, AsyncMock]:
    fake_store = MagicMock()
    fake_store.query = MagicMock(return_value=hits)

    fake_embedder = MagicMock()
    # Embedder.embed is async; return a single 3-d vector for any input.
    embed_mock = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    fake_embedder.embed = embed_mock

    retriever = STRRetriever(store=fake_store, embedder=fake_embedder, top_k=top_k)
    return retriever, fake_store, embed_mock


@pytest.mark.asyncio
async def test_retrieve_for_str_filters_by_str_code() -> None:
    """retrieve_for_str must pass filter_str_code through to the store, even
    when the (mocked) store would happily return chunks for other STRs.
    """
    hits = [
        _hit(str_code="STR 2.02.01:2004", text="alpha"),
        _hit(str_code="STR 2.02.01:2004", text="beta", chunk_index=1),
    ]
    retriever, fake_store, _ = _make_retriever(hits)

    out = await retriever.retrieve_for_str("STR 2.02.01:2004", "lubu aukstis butuose")

    assert len(out) == 2
    fake_store.query.assert_called_once()
    kwargs = fake_store.query.call_args.kwargs
    assert kwargs["filter_str_code"] == "STR 2.02.01:2004"
    assert all(c.str_code == "STR 2.02.01:2004" for c in out)


@pytest.mark.asyncio
async def test_retrieve_open_does_not_filter() -> None:
    """retrieve_open must reach the store with filter_str_code=None so the
    embedding space drives the match across every ingested STR.
    """
    hits = [_hit(str_code="STR 2.05.04:2003", text="apkrovos")]
    retriever, fake_store, _ = _make_retriever(hits)

    out = await retriever.retrieve_open("sniego apkrova")

    assert len(out) == 1
    fake_store.query.assert_called_once()
    kwargs = fake_store.query.call_args.kwargs
    assert kwargs["filter_str_code"] is None


@pytest.mark.asyncio
async def test_returns_retrieved_chunks_with_similarity() -> None:
    """Distance → similarity inversion must propagate, and metadata must
    flatten onto the RetrievedChunk dataclass.
    """
    hits = [
        _hit(
            str_code="STR 2.02.01:2004",
            text="article 4.3 text",
            start_page=12,
            end_page=13,
            article="4.3",
            distance=0.1,
        )
    ]
    retriever, _, _ = _make_retriever(hits)

    out = await retriever.retrieve_for_str("STR 2.02.01:2004", "query")

    assert len(out) == 1
    chunk = out[0]
    assert isinstance(chunk, RetrievedChunk)
    assert chunk.text == "article 4.3 text"
    assert chunk.start_page == 12
    assert chunk.end_page == 13
    assert chunk.article == "4.3"
    # similarity = 1 - distance
    assert chunk.similarity == pytest.approx(0.9, abs=1e-6)


@pytest.mark.asyncio
async def test_top_k_default_and_override() -> None:
    """Constructor default applies when top_k is omitted; per-call top_k
    overrides it.
    """
    retriever, fake_store, _ = _make_retriever([], top_k=5)

    await retriever.retrieve_open("query")
    assert fake_store.query.call_args.kwargs["top_k"] == 5

    fake_store.query.reset_mock()
    await retriever.retrieve_open("query", top_k=10)
    assert fake_store.query.call_args.kwargs["top_k"] == 10

    # And the str-filtered variant respects the same override.
    fake_store.query.reset_mock()
    await retriever.retrieve_for_str("STR X", "query", top_k=2)
    assert fake_store.query.call_args.kwargs["top_k"] == 2


@pytest.mark.asyncio
async def test_empty_query_short_circuits() -> None:
    """Empty / whitespace-only queries shouldn't hit the embedder or store."""
    retriever, fake_store, embed_mock = _make_retriever([])

    out = await retriever.retrieve_open("   ")

    assert out == []
    embed_mock.assert_not_called()
    fake_store.query.assert_not_called()
