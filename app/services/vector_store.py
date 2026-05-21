"""Chroma-backed vector store for STR regulation chunks.

Used by:
- ``scripts/ingest_str.py`` (python-be-e6n.5) — one-shot embed + upsert
- The Stage-2 retriever (python-be-e6n.6) — top-K similarity lookup
- ``RAGChecker`` (python-be-e6n.7) — wraps the retriever into the runtime path

The store is intentionally NOT imported by ``app.api`` or
``app.services.compliance_checker`` today: the ``BasicChecker`` keeps working
as before, and ``chromadb`` is a dev-only ``[rag]`` extra. Once e6n.7 wires
``RAGChecker`` into the API, ``chromadb`` will graduate into runtime deps and
this module becomes a hot path.

Schema
------
Single Chroma collection ``str_chunks`` with:

* id        — ``"<str_code>::<chunk_index>"`` (deterministic; re-running the
              ingest overwrites instead of duplicating)
* document  — chunk text (kept so the retriever can return it without a
              separate registry round-trip)
* embedding — ``text-embedding-3-small`` 1536-d vector
* metadata  — ``str_code``, ``chunk_index``, ``start_page``, ``end_page``,
              ``article`` (optional), ``agent_routing``

Filtering
---------
``query()`` exposes two ``where`` filters:

* ``filter_str_code`` — exact match on ``str_code``. Used by the retriever
  when the project doc itself cites a specific STR (e.g. tag-based routing).
* ``filter_agent_routing`` — exact match on ``agent_routing`` (Epic 3).
  Specialized agents query only their own corner of the regulation space.

Both can be combined with ``$and`` semantics. We deliberately DON'T expose
arbitrary Chroma where-clauses — keeping this surface tight makes the
retriever and orchestrator easy to reason about.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.str_chunker import STRChunk

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "str_chunks"


class STRVectorStore:
    """Persistent Chroma store for STR regulation chunks.

    Defaults to a disk-backed client at ``vector_store/`` (gitignored). For
    tests, pass ``client=chromadb.EphemeralClient()`` to get an in-memory
    instance with the same semantics.
    """

    def __init__(
        self,
        persist_dir: Path | str = Path("vector_store"),
        *,
        client: Any | None = None,
        collection_name: str = _COLLECTION_NAME,
    ) -> None:
        # Lazy import — chromadb lives in the [rag] extra, runtime code shouldn't
        # have to install it. Importing at module level would break ``uv sync``
        # without ``--extra rag``.
        import chromadb

        self._collection_name = collection_name
        if client is not None:
            self._client = client
        else:
            persist_path = Path(persist_dir)
            persist_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(persist_path))

        # get_or_create keeps us idempotent across runs and friendly to tests
        # that share an EphemeralClient between assertions.
        self._collection = self._client.get_or_create_collection(name=collection_name)

    # --- mutation ----------------------------------------------------------

    def upsert(
        self,
        chunks: list[STRChunk],
        embeddings: list[list[float]],
        agent_routing: str,
    ) -> None:
        """Insert or overwrite chunks. ID format ``<str_code>::<chunk_index>``.

        ``agent_routing`` comes from the registry entry — we store it on every
        chunk so Epic 3's specialized agents can filter without rejoining.
        Re-running with the same chunks safely overwrites; chunk count is
        stable (it's keyed on chunk_index, not content hash).
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) length mismatch"
            )
        if not chunks:
            return

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for chunk in chunks:
            ids.append(f"{chunk.str_code}::{chunk.chunk_index}")
            documents.append(chunk.text)
            md: dict[str, Any] = {
                "str_code": chunk.str_code,
                "chunk_index": chunk.chunk_index,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "agent_routing": agent_routing,
            }
            # Chroma metadata cannot store None; only set article when known.
            if chunk.article is not None:
                md["article"] = chunk.article
            metadatas.append(md)

        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def reset(self) -> None:
        """Drop and recreate the collection. Used by ``ingest_str.py --reset``."""
        try:
            self._client.delete_collection(name=self._collection_name)
        except Exception:  # pragma: no cover — Chroma raises NotFound subclasses
            logger.debug("delete_collection: collection didn't exist, ignoring")
        self._collection = self._client.get_or_create_collection(name=self._collection_name)

    # --- query -------------------------------------------------------------

    def query(
        self,
        embedding: list[float],
        top_k: int = 5,
        filter_str_code: str | None = None,
        filter_agent_routing: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return top-K matching chunks with metadata + similarity score.

        Result shape (one dict per hit, ordered best→worst):

            {
                "id": "STR 2.02.01:2004::3",
                "text": "...",
                "metadata": {"str_code": ..., "start_page": ..., ...},
                "distance": 0.213,   # cosine distance, lower = closer
            }

        Filters use exact-match Chroma ``where`` clauses; combine with ``$and``
        when both are provided. Returns [] if the collection is empty.
        """
        if self._collection.count() == 0:
            return []

        where: dict[str, Any] | None = None
        clauses: list[dict[str, Any]] = []
        if filter_str_code is not None:
            clauses.append({"str_code": filter_str_code})
        if filter_agent_routing is not None:
            clauses.append({"agent_routing": filter_agent_routing})
        if len(clauses) == 1:
            where = clauses[0]
        elif len(clauses) > 1:
            where = {"$and": clauses}

        result = self._collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where=where,
        )

        # Chroma returns parallel lists nested one level (per query embedding).
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        out: list[dict[str, Any]] = []
        for idx, doc_id in enumerate(ids):
            out.append(
                {
                    "id": doc_id,
                    "text": documents[idx] if idx < len(documents) else "",
                    "metadata": metadatas[idx] if idx < len(metadatas) else {},
                    "distance": distances[idx] if idx < len(distances) else None,
                }
            )
        return out

    # --- diagnostics -------------------------------------------------------

    def count(self) -> dict[str, int]:
        """Return ``{"_total": N, "<str_code>": n, ...}`` for the ingest report.

        Pulls only metadata (no embeddings, no documents) so this stays cheap
        even at full 30-STR scale (~6k vectors).
        """
        total = self._collection.count()
        result: dict[str, int] = {"_total": total}
        if total == 0:
            return result

        # Chroma's `get` defaults to all rows when no ids/where are given;
        # we explicitly request only metadatas to avoid loading embeddings.
        rows = self._collection.get(include=["metadatas"])
        metadatas = rows.get("metadatas") or []
        per_code = Counter(
            (md or {}).get("str_code", "<unknown>") for md in metadatas
        )
        for code, n in sorted(per_code.items()):
            result[code] = n
        return result
