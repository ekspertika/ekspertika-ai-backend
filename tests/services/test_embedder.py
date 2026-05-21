"""Tests for app.services.embedder. All OpenAI calls are mocked."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.embedder import Embedder


def _fake_response(batch: list[str]) -> SimpleNamespace:
    """Mimic an OpenAI embeddings response.

    Each input gets a 4-dim vector whose first component encodes its position in the
    batch and whose remaining components encode the input string's length — enough
    signal to verify ordering survives batching.
    """
    data = [
        SimpleNamespace(index=i, embedding=[float(i), float(len(text)), 0.0, 0.0])
        for i, text in enumerate(batch)
    ]
    return SimpleNamespace(data=data)


def _build_embedder() -> tuple[Embedder, AsyncMock]:
    """Build an Embedder with a mocked AsyncOpenAI client. Returns (embedder, create_mock)."""
    create_mock = AsyncMock(side_effect=lambda model, input: _fake_response(input))
    fake_client = MagicMock()
    fake_client.embeddings.create = create_mock
    embedder = Embedder(model="text-embedding-3-small", client=fake_client)
    return embedder, create_mock


class TestEmbedderBatching:
    @pytest.mark.asyncio
    async def test_250_inputs_produce_three_batches(self) -> None:
        embedder, create_mock = _build_embedder()
        texts = [f"text-{i}" for i in range(250)]

        vectors = await embedder.embed(texts)

        assert len(vectors) == 250
        # 100 + 100 + 50 = 3 calls
        assert create_mock.await_count == 3
        # And the batch sizes should be exactly [100, 100, 50] in order.
        batch_sizes = [len(call.kwargs["input"]) for call in create_mock.await_args_list]
        assert batch_sizes == [100, 100, 50]

    @pytest.mark.asyncio
    async def test_output_order_preserved_across_batches(self) -> None:
        embedder, _ = _build_embedder()
        # Use distinct lengths so each text is uniquely identifiable in its embedding.
        texts = [f"text-{i}-{'x' * (i % 7)}" for i in range(250)]

        vectors = await embedder.embed(texts)

        assert len(vectors) == len(texts)
        # _fake_response encodes position-in-batch as vector[0] and input length as
        # vector[1]. The position component cycles per-batch (0..99, 0..99, 0..49) but
        # the length component is unique to the input text and must line up per index.
        for i, vec in enumerate(vectors):
            assert vec[1] == float(len(texts[i])), f"order broken at index {i}"

    @pytest.mark.asyncio
    async def test_empty_input_short_circuits(self) -> None:
        embedder, create_mock = _build_embedder()
        result = await embedder.embed([])
        assert result == []
        create_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_batch_under_limit(self) -> None:
        embedder, create_mock = _build_embedder()
        texts = [f"t-{i}" for i in range(42)]
        vectors = await embedder.embed(texts)

        assert len(vectors) == 42
        assert create_mock.await_count == 1
        assert len(create_mock.await_args_list[0].kwargs["input"]) == 42

    @pytest.mark.asyncio
    async def test_exactly_100_inputs_one_batch(self) -> None:
        embedder, create_mock = _build_embedder()
        texts = [f"t-{i}" for i in range(100)]
        vectors = await embedder.embed(texts)

        assert len(vectors) == 100
        assert create_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_exactly_200_inputs_two_batches(self) -> None:
        embedder, create_mock = _build_embedder()
        texts = [f"t-{i}" for i in range(200)]
        vectors = await embedder.embed(texts)

        assert len(vectors) == 200
        assert create_mock.await_count == 2

    @pytest.mark.asyncio
    async def test_model_passed_to_api(self) -> None:
        embedder, create_mock = _build_embedder()
        await embedder.embed(["hello"])
        assert create_mock.await_args.kwargs["model"] == "text-embedding-3-small"

    @pytest.mark.asyncio
    async def test_handles_unsorted_response_data(self) -> None:
        """OpenAI guarantees order, but we defensively sort by index — verify that works."""
        # Replace create with one that returns shuffled data.
        def shuffled(model: str, input: list[str]) -> SimpleNamespace:  # noqa: A002
            response = _fake_response(input)
            response.data = list(reversed(response.data))
            return response

        create_mock = AsyncMock(side_effect=shuffled)
        fake_client = MagicMock()
        fake_client.embeddings.create = create_mock
        embedder = Embedder(model="text-embedding-3-small", client=fake_client)

        texts = [f"text-{'x' * i}" for i in range(5)]
        vectors = await embedder.embed(texts)

        for i, vec in enumerate(vectors):
            assert vec[1] == float(len(texts[i]))
