"""Section 10: CircuitBreaker + retry_with_jitter unit tests."""

from __future__ import annotations

import asyncio

import pytest
from fii_shared.resilience import (
    BreakerState,
    CircuitBreaker,
    CircuitOpenError,
    retry_with_jitter,
)


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold():
    cb = CircuitBreaker(name="t", failure_threshold=3, cooldown_s=60)

    async def fail():
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(fail)
    assert cb.state is BreakerState.OPEN
    with pytest.raises(CircuitOpenError):
        await cb.call(fail)


@pytest.mark.asyncio
async def test_breaker_half_open_then_close_on_success():
    cb = CircuitBreaker(name="t", failure_threshold=2, cooldown_s=0.01)

    async def fail():
        raise RuntimeError("boom")

    async def ok():
        return "yes"

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(fail)
    assert cb.state is BreakerState.OPEN

    # Wait past cooldown → state transitions to half_open on next read.
    await asyncio.sleep(0.02)
    assert cb.state is BreakerState.HALF_OPEN

    # A successful call closes the breaker.
    result = await cb.call(ok)
    assert result == "yes"
    assert cb.state is BreakerState.CLOSED


@pytest.mark.asyncio
async def test_breaker_half_open_failure_reopens():
    cb = CircuitBreaker(name="t", failure_threshold=2, cooldown_s=0.01)

    async def fail():
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(fail)

    await asyncio.sleep(0.02)
    assert cb.state is BreakerState.HALF_OPEN
    with pytest.raises(RuntimeError):
        await cb.call(fail)
    assert cb.state is BreakerState.OPEN


@pytest.mark.asyncio
async def test_breaker_snapshot_shape():
    cb = CircuitBreaker(name="snap", failure_threshold=10, cooldown_s=30)
    snap = cb.snapshot()
    assert snap["name"] == "snap"
    assert snap["state"] == "closed"
    assert snap["consecutive_failures"] == 0
    assert snap["cooldown_remaining_s"] == 0.0


@pytest.mark.asyncio
async def test_retry_with_jitter_succeeds_after_failures():
    counter = {"n": 0}

    async def flaky():
        counter["n"] += 1
        if counter["n"] < 3:
            raise RuntimeError("transient")
        return counter["n"]

    result = await retry_with_jitter(flaky, retries=5, base_delay_s=0.001, max_delay_s=0.01)
    assert result == 3


@pytest.mark.asyncio
async def test_retry_with_jitter_skips_not_retry_on():
    class Permanent(Exception):
        pass

    async def perm():
        raise Permanent("4xx")

    with pytest.raises(Permanent):
        await retry_with_jitter(
            perm,
            retries=3,
            base_delay_s=0.001,
            not_retry_on=(Permanent,),
        )


@pytest.mark.asyncio
async def test_retry_with_jitter_raises_last_exception():
    async def always_fail():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await retry_with_jitter(always_fail, retries=2, base_delay_s=0.001, max_delay_s=0.01)
