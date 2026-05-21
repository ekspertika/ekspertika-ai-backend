"""Tests for app.services.vector_store.STRVectorStore.

Approach: use Chroma's ``EphemeralClient`` (in-memory, no disk) so each test
gets a clean store with the same Chroma semantics as production.

These tests REQUIRE the ``rag`` extra to be installed:

    uv sync --extra rag
    uv run python -m pytest tests/services/test_vector_store.py -v

If ``chromadb`` isn't importable, the whole module is skipped — running the
default ``uv run pytest`` (no extras) won't fail.
"""

from __future__ import annotations

import uuid

import pytest

# Skip the whole module cleanly when [rag] extra isn't installed (the runtime
# image deliberately omits it). The other 34 tests stay green either way.
pytest.importorskip("chromadb")

import chromadb  # noqa: E402

from app.services.str_chunker import STRChunk  # noqa: E402
from app.services.vector_store import STRVectorStore  # noqa: E402


def _vec(seed: float, dim: int = 8) -> list[float]:
    """Build a deterministic, distinct unit-ish vector for tests.

    Each chunk gets a unique vector so cosine distances are strictly ordered;
    keeps assertions about top-1 hits stable.
    """
    return [seed * (i + 1) * 0.1 for i in range(dim)]


def _make_chunk(
    str_code: str,
    chunk_index: int,
    text: str = "lorem ipsum",
    article: str | None = "1.1",
    start_page: int = 1,
    end_page: int = 2,
) -> STRChunk:
    return STRChunk(
        str_code=str_code,
        chunk_index=chunk_index,
        text=text,
        start_page=start_page,
        end_page=end_page,
        article=article,
    )


@pytest.fixture
def store() -> STRVectorStore:
    """Fresh in-memory store per test — no disk, no cross-test bleed.

    Note: Chroma ``EphemeralClient`` instances share an underlying System
    singleton inside one process, so two ephemeral clients can see each
    other's collections by name. We give every test its own UUID-suffixed
    collection so they stay isolated even though they may share storage.
    """
    client = chromadb.EphemeralClient()
    coll_name = f"test_str_chunks_{uuid.uuid4().hex[:8]}"
    return STRVectorStore(client=client, collection_name=coll_name)


class TestUpsertAndQuery:
    def test_upsert_then_query_returns_inserted_chunks(self, store: STRVectorStore) -> None:
        chunks = [
            _make_chunk("STR 2.02.01:2004", i, text=f"chunk {i} text") for i in range(5)
        ]
        embeddings = [_vec(seed=i + 1) for i in range(5)]
        store.upsert(chunks=chunks, embeddings=embeddings, agent_routing="structural")

        # Query with the third chunk's vector — top-1 should be that exact chunk.
        results = store.query(embedding=embeddings[2], top_k=3)
        assert len(results) == 3
        top = results[0]
        assert top["id"] == "STR 2.02.01:2004::2"
        assert top["text"] == "chunk 2 text"
        assert top["metadata"]["str_code"] == "STR 2.02.01:2004"
        assert top["metadata"]["agent_routing"] == "structural"
        assert top["metadata"]["chunk_index"] == 2
        assert top["distance"] is not None
        # Identity match should be (near-)zero distance.
        assert top["distance"] == pytest.approx(0.0, abs=1e-5)


class TestFiltering:
    def test_filter_by_str_code(self, store: STRVectorStore) -> None:
        a_chunks = [_make_chunk("STR 2.02.01:2004", i) for i in range(3)]
        b_chunks = [_make_chunk("STR 2.05.05:2005", i) for i in range(3)]
        a_vecs = [_vec(seed=i + 1) for i in range(3)]
        b_vecs = [_vec(seed=i + 10) for i in range(3)]
        store.upsert(a_chunks, a_vecs, agent_routing="structural")
        store.upsert(b_chunks, b_vecs, agent_routing="structural")

        # Query with a B-vector but filter for A — must only return A chunks.
        results = store.query(
            embedding=b_vecs[0],
            top_k=10,
            filter_str_code="STR 2.02.01:2004",
        )
        assert len(results) == 3
        assert all(r["metadata"]["str_code"] == "STR 2.02.01:2004" for r in results)

    def test_filter_by_agent_routing(self, store: STRVectorStore) -> None:
        # Two STRs in different agent_routing buckets — Stage-3 specialized agents
        # should be able to query just their own corner.
        store.upsert(
            [_make_chunk("STR 2.01.01:1999", i) for i in range(2)],
            [_vec(seed=i + 1) for i in range(2)],
            agent_routing="fire_safety",
        )
        store.upsert(
            [_make_chunk("STR 2.01.02:2016", i) for i in range(2)],
            [_vec(seed=i + 5) for i in range(2)],
            agent_routing="energy",
        )

        results = store.query(
            embedding=_vec(seed=1),
            top_k=10,
            filter_agent_routing="energy",
        )
        assert len(results) == 2
        assert all(r["metadata"]["agent_routing"] == "energy" for r in results)


class TestCount:
    def test_count_per_str_code(self, store: STRVectorStore) -> None:
        store.upsert(
            [_make_chunk("STR 2.02.01:2004", i) for i in range(4)],
            [_vec(seed=i + 1) for i in range(4)],
            agent_routing="structural",
        )
        store.upsert(
            [_make_chunk("STR 2.05.05:2005", i) for i in range(2)],
            [_vec(seed=i + 10) for i in range(2)],
            agent_routing="structural",
        )

        counts = store.count()
        assert counts["_total"] == 6
        assert counts["STR 2.02.01:2004"] == 4
        assert counts["STR 2.05.05:2005"] == 2


class TestIdempotency:
    def test_re_upsert_overwrites_not_duplicates(self, store: STRVectorStore) -> None:
        chunks = [_make_chunk("STR 2.02.01:2004", i) for i in range(3)]
        vecs = [_vec(seed=i + 1) for i in range(3)]

        store.upsert(chunks, vecs, agent_routing="structural")
        assert store.count()["_total"] == 3

        # Same chunks again — IDs match, count must stay at 3.
        store.upsert(chunks, vecs, agent_routing="structural")
        counts = store.count()
        assert counts["_total"] == 3
        assert counts["STR 2.02.01:2004"] == 3
