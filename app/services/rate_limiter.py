"""Token-budget pacing for OpenAI calls. Port of nextjs-fe/lib/ai/rate-limiter.ts.

Sliding 60s window TPM accounting with a 70% threshold. Use it as a proactive
throttle in front of `tenacity` retries — it prevents most 429s from happening
in the first place; the retry layer still handles the residue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Rough token estimator: 1 token ~ 4 chars * 1.4 safety multiplier.

    Matches the FE implementation in lib/ai/rate-limiter.ts. The 40% buffer
    over the naive chars/4 estimate compensates for tokenizer variability and
    avoids tight-edge 429s when many checks run back-to-back.
    """
    return int((len(text) / 4) * 1.4) + 1


class TokenBudget:
    """Sliding-window TPM accounting.

    Call ``acquire(tokens)`` before each LLM request. If the running 60s window
    plus the new tokens would exceed 70% of the configured TPM limit, the call
    awaits until the window resets. Use ``reset_window()`` from a retry hook to
    forget accumulated usage when a 429 forces a backoff.
    """

    def __init__(self, limit_tpm: int) -> None:
        self._limit_tpm = limit_tpm
        self._used = 0
        self._window_start = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def used_tokens(self) -> int:
        return self._used

    @property
    def limit_tpm(self) -> int:
        return self._limit_tpm

    async def acquire(self, tokens: int) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._window_start

            if elapsed >= 60.0:
                self._used = 0
                self._window_start = now

            threshold = self._limit_tpm * 0.70

            if self._used + tokens > threshold:
                remaining = 60.0 - (time.monotonic() - self._window_start) + 2.0
                if remaining < 0:
                    remaining = 0.0
                logger.info(
                    "[rate-limiter] Budget at %d/%d TPM. Waiting %ds for window reset.",
                    self._used,
                    self._limit_tpm,
                    round(remaining),
                )
                await asyncio.sleep(remaining)
                self._used = 0
                self._window_start = time.monotonic()

            self._used += tokens

    def reset_window(self) -> None:
        """Force a window reset — call this from a retry hook after a 429."""
        self._used = 0
        self._window_start = time.monotonic()


# Module-level singleton, configurable via env. Defaults match OpenAI Tier 1
# gpt-4o-mini (200K TPM).
token_budget = TokenBudget(int(os.getenv("OPENAI_TPM_LIMIT", "200000")))
