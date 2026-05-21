"""Async OpenAI embedder for the RAG vector store.

Wraps ``text-embedding-3-small`` (1536 dims, ~$0.02 / 1M tokens) with automatic 100-input
batching (the OpenAI API per-request limit) and tenacity-backed retries on transient
errors. ``embed`` preserves input order across batches so callers can zip results back
onto their source chunks safely.
"""

from __future__ import annotations

from openai import APIConnectionError, APIError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.config import Config

# OpenAI's documented per-request cap for the embeddings endpoint.
_OPENAI_BATCH_LIMIT = 100


class Embedder:
    """Async OpenAI embedder. Batches up to 100 inputs per request (OpenAI limit)."""

    def __init__(
        self,
        model: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model or Config.EMBEDDING_MODEL
        self.client = client or AsyncOpenAI(api_key=Config.OPENAI_API_KEY)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts; returns vectors in the same order. Batches automatically."""
        if not texts:
            return []

        results: list[list[float]] = []
        for start in range(0, len(texts), _OPENAI_BATCH_LIMIT):
            batch = texts[start : start + _OPENAI_BATCH_LIMIT]
            batch_vectors = await self._embed_batch(batch)
            results.extend(batch_vectors)
        return results

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (APIConnectionError, RateLimitError, APIError),
        ),
        reraise=True,
    )
    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Single API call. OpenAI guarantees response order matches input order."""
        response = await self.client.embeddings.create(
            model=self.model,
            input=batch,
        )
        # OpenAI returns data sorted by index, but defensively sort anyway.
        ordered = sorted(response.data, key=lambda d: d.index)
        return [d.embedding for d in ordered]
