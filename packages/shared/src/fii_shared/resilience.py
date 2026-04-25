"""Resilience primitives shared across data-clients and the LLM model wrapper.

CircuitBreaker — opens after N consecutive failures, stays open for cooldown_s,
then half-opens (one trial). Per-instance state; one breaker per external service.

retry_with_jitter — async retry decorator. Exponential backoff with full jitter
(AWS guidance). Only retries the configured exception types; never retries 4xx.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

import structlog

log = structlog.get_logger(__name__)

T = TypeVar("T")


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is short-circuited because the breaker is open."""


@dataclass
class CircuitBreaker:
    """Trip on N consecutive failures; cool down for `cooldown_s` then probe once."""

    name: str
    failure_threshold: int = 5
    cooldown_s: float = 60.0

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)

    @property
    def state(self) -> BreakerState:
        # Auto-transition open → half_open once the cooldown has elapsed.
        if (
            self._state is BreakerState.OPEN
            and time.monotonic() - self._opened_at >= self.cooldown_s
        ):
            self._state = BreakerState.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        if self._state in (BreakerState.HALF_OPEN, BreakerState.OPEN):
            log.info("circuit_breaker_closed", name=self.name)
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._state is BreakerState.HALF_OPEN:
            self._open()
            return
        if self._consecutive_failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        if self._state is not BreakerState.OPEN:
            log.warning(
                "circuit_breaker_opened",
                name=self.name,
                failures=self._consecutive_failures,
                cooldown_s=self.cooldown_s,
            )
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()

    async def call(self, fn: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """Execute fn under the breaker. Raises CircuitOpenError when open."""
        s = self.state
        if s is BreakerState.OPEN:
            raise CircuitOpenError(f"{self.name} circuit is open")
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self._consecutive_failures,
            "cooldown_remaining_s": (
                max(0.0, self.cooldown_s - (time.monotonic() - self._opened_at))
                if self._state is BreakerState.OPEN
                else 0.0
            ),
        }


# --- Retry with jitter -------------------------------------------------------------------


async def retry_with_jitter(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    retries: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    not_retry_on: tuple[type[BaseException], ...] = (),
    name: str = "retry",
    **kwargs: Any,
) -> T:
    """AWS-style full-jitter retry. Sleeps random.uniform(0, min(max, base * 2^attempt))."""
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return await fn(*args, **kwargs)
        except not_retry_on:
            raise
        except retry_on as exc:
            last_exc = exc
            if attempt == retries:
                break
            cap = min(max_delay_s, base_delay_s * (2**attempt))
            delay = random.uniform(0.0, cap)
            log.info(
                "retrying",
                name=name,
                attempt=attempt + 1,
                max_attempts=retries + 1,
                delay_s=round(delay, 3),
                error=str(exc)[:200],
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
