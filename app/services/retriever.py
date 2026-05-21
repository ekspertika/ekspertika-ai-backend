"""High-level STR retriever.

Wraps :class:`STRVectorStore` + :class:`Embedder` so callers can go directly
from a free-form query string to a list of :class:`RetrievedChunk`s annotated
with str_code / page / article / similarity. This is the integration surface
``RAGChecker`` (python-be-e6n.7) and the Stage-3 specialised agents will use.

Two retrieval modes are exposed:

* :meth:`retrieve_for_str` — exact-match filter on ``str_code``. Use when the
  current ``CheckItem.code`` cleanly maps onto a known STR (``check_type='str'``).
* :meth:`retrieve_open` — no filter, semantic search across the whole store.
  Use when the item references something laterally (a law / standard /
  required document) — the relevant STR text might live anywhere.

Similarity score
----------------
Chroma returns cosine *distance* (lower = closer). We expose ``similarity =
1 - distance`` so callers can think in the more intuitive 0-1 range with
"higher is better". Distances on Chroma's default cosine space are bounded
in [0, 2]; for typical near-neighbour retrieval results stay comfortably in
[0, 1] so the inversion is safe to use as-is for ranking and prompt display.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.embedder import Embedder

if TYPE_CHECKING:
    from app.services.vector_store import STRVectorStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    """Single retrieval hit, flattened for prompt construction."""

    str_code: str
    text: str
    start_page: int
    end_page: int
    article: str | None
    similarity: float


class STRRetriever:
    """High-level retrieval: query terms → top-K STR chunks with metadata.

    Wraps STRVectorStore + Embedder. The vector store holds chunks indexed by
    str_code; this retriever knows when to filter (item.code matches a known
    STR) vs when to do an open semantic search (laws/standards/documents
    don't have STR-coded knowledge in the store).
    """

    def __init__(
        self,
        store: STRVectorStore | None = None,
        embedder: Embedder | None = None,
        top_k: int = 5,
    ) -> None:
        # Lazy default so importing this module doesn't force chromadb at
        # parse time — keeps it consistent with vector_store's lazy import.
        if store is None:
            from app.services.vector_store import STRVectorStore as _Store

            store = _Store()
        self._store = store
        self._embedder = embedder or Embedder()
        self._top_k = top_k

    async def retrieve_for_str(
        self,
        str_code: str,
        query: str,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Filter by ``str_code``, embed ``query``, return top-K hits.

        Used for ``check_type='str'`` items where the regulation we're
        evaluating against is known up front.
        """
        return await self._retrieve(query, top_k=top_k, filter_str_code=str_code)

    async def retrieve_open(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """No str_code filter — semantic search across the whole store.

        Used for ``law`` / ``standard`` / ``document`` items: laws and
        standards reference STR articles indirectly, so we let the embedding
        space do the matching across all ingested STRs.
        """
        return await self._retrieve(query, top_k=top_k, filter_str_code=None)

    async def _retrieve(
        self,
        query: str,
        *,
        top_k: int | None,
        filter_str_code: str | None,
    ) -> list[RetrievedChunk]:
        if not query or not query.strip():
            return []

        k = top_k if top_k is not None else self._top_k
        embeddings = await self._embedder.embed([query])
        if not embeddings:
            return []
        query_vec = embeddings[0]

        hits = self._store.query(
            embedding=query_vec,
            top_k=k,
            filter_str_code=filter_str_code,
        )
        return [_to_retrieved_chunk(hit) for hit in hits]


def _to_retrieved_chunk(hit: dict[str, Any]) -> RetrievedChunk:
    """Map a STRVectorStore.query() hit dict onto a RetrievedChunk.

    The store hands back cosine *distance*; we flip it to similarity here so
    callers don't have to remember the inversion.
    """
    md = hit.get("metadata") or {}
    distance = hit.get("distance")
    similarity = 1.0 - float(distance) if distance is not None else 0.0
    return RetrievedChunk(
        str_code=str(md.get("str_code", "")),
        text=str(hit.get("text", "")),
        start_page=int(md.get("start_page", 0) or 0),
        end_page=int(md.get("end_page", 0) or 0),
        article=md.get("article"),
        similarity=similarity,
    )
