"""Tests for app.services.rate_limiter.

Sleep is mocked everywhere — the suite must run in well under a second.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.rate_limiter import TokenBudget, estimate_tokens


class TestEstimateTokens:
    def test_uses_safety_multiplier(self) -> None:
        # 100 / 4 = 25 base; * 1.4 = 35; +1 for the safety floor = 36.
        assert estimate_tokens("a" * 100) == 36

    def test_empty_string(self) -> None:
        assert estimate_tokens("") == 1

    def test_scales_linearly(self) -> None:
        small = estimate_tokens("a" * 1_000)
        big = estimate_tokens("a" * 10_000)
        # Big should be ~10x small. Allow 10-token slop because the +1 floor
        # does not compose linearly across the two estimates.
        assert abs(big - small * 10) <= 10


class TestAcquire:
    @pytest.mark.asyncio
    async def test_under_threshold_returns_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        budget = TokenBudget(limit_tpm=200_000)
        await budget.acquire(100)

        assert sleep_calls == []
        assert budget.used_tokens == 100

    @pytest.mark.asyncio
    async def test_over_threshold_waits_for_window_reset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        budget = TokenBudget(limit_tpm=1_000)
        # Pre-fill to 71% — strictly above the 70% threshold so the next
        # acquire of any positive value triggers a wait.
        budget._used = 710  # noqa: SLF001 — internal seed for the test
        await budget.acquire(100)

        # Exactly one sleep was issued and the window was reset afterwards.
        assert len(sleep_calls) == 1
        assert sleep_calls[0] > 0
        # After reset, only the current acquisition counts.
        assert budget.used_tokens == 100

    @pytest.mark.asyncio
    async def test_window_auto_resets_after_60s(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Drive ``time.monotonic`` so we can simulate the 60s rollover without
        # waiting. Constructor seeds at t=0; acquire() reads t=70 — past the
        # 60s window — and the budget should auto-reset without sleeping.
        clock = {"t": 0.0}
        import app.services.rate_limiter as rl

        monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])

        async def fake_sleep(_seconds: float) -> None:
            pytest.fail("sleep should not have been called after window auto-reset")

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        budget = TokenBudget(limit_tpm=1_000)
        budget._used = 900  # noqa: SLF001 — would be over threshold...
        clock["t"] = 70.0  # advance the simulated clock past the 60s window
        await budget.acquire(50)  # ...but the elapsed clock auto-resets it.

        assert budget.used_tokens == 50


class TestResetWindow:
    def test_reset_window_clears_used(self) -> None:
        budget = TokenBudget(limit_tpm=1_000)
        budget._used = 900  # noqa: SLF001 — internal seed for the test
        budget.reset_window()
        assert budget.used_tokens == 0
