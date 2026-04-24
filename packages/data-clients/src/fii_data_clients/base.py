"""Common primitives for every data provider client.

Every concrete client (Polygon, FMP, FRED, Finnhub, ...) should subclass `BaseHttpClient`
to get: httpx.AsyncClient lifecycle, tenacity retries with jittered exponential backoff,
a token-bucket rate limiter, structured request/response logging with key redaction,
and a simple cost-accounting hook.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Self

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = structlog.get_logger(__name__)


# --- Rate limiting ------------------------------------------------------------------------


class TokenBucket:
    """Async token-bucket rate limiter. One instance per provider per client."""

    def __init__(self, *, rate_per_sec: float, burst: int | None = None) -> None:
        self.rate = rate_per_sec
        self.capacity = float(burst if burst is not None else max(1, int(rate_per_sec)))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                sleep_for = (cost - self._tokens) / self.rate
                await asyncio.sleep(sleep_for)


# --- Redacted headers ---------------------------------------------------------------------


_SECRET_HEADER_KEYS = {
    "authorization",
    "x-api-key",
    "x-apikey",
    "apikey",
    "x-finnhub-token",
    "x-polygon-api-key",
}
_SECRET_QUERY_KEYS = {"apikey", "api_key", "token"}


def _redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _SECRET_HEADER_KEYS:
            out[k] = f"<redacted:{len(v)}>"
        else:
            out[k] = v
    return out


def _redact_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    out: dict[str, Any] = {}
    for k, v in params.items():
        if k.lower() in _SECRET_QUERY_KEYS:
            out[k] = f"<redacted:{len(str(v))}>"
        else:
            out[k] = v
    return out


# --- Cost accounting ----------------------------------------------------------------------


@dataclass
class CostTally:
    """Rough per-client cost tracker. Concrete clients update these as they go."""

    provider: str
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    def add_request(self, *, tokens_in: int = 0, tokens_out: int = 0, usd: float = 0.0) -> None:
        self.requests += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.usd += usd


# --- Base HTTP client ---------------------------------------------------------------------


class ProviderError(Exception):
    """Base class for provider-side errors."""


class RateLimitedError(ProviderError):
    """Raised when a provider returns 429."""


class UpstreamError(ProviderError):
    """Raised when a provider returns 5xx."""


_RETRYABLE = (httpx.TransportError, httpx.ReadTimeout, RateLimitedError, UpstreamError)


class BaseHttpClient:
    """Async HTTP client with retries, rate-limit, redaction, and cost tally."""

    provider: str = "base"
    default_timeout: float = 20.0
    default_rate_per_sec: float = 5.0
    default_burst: int = 10
    max_attempts: int = 4

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        default_headers: Mapping[str, str] | None = None,
        rate_per_sec: float | None = None,
        burst: int | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._default_headers = dict(default_headers or {})
        self._bucket = TokenBucket(
            rate_per_sec=rate_per_sec or self.default_rate_per_sec,
            burst=burst or self.default_burst,
        )
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout or self.default_timeout
        self.cost = CostTally(provider=self.provider)
        self._log = log.bind(provider=self.provider)

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self._timeout,
            headers=self._default_headers,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- Subclass hook: attach provider auth (query param or header) ----------------------

    def _apply_auth(
        self, headers: dict[str, str], params: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """Override in subclasses to inject API key into headers or params."""
        return headers, params

    # --- The request loop -----------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("BaseHttpClient must be used as an async context manager")

        merged_headers = dict(headers or {})
        merged_params = dict(params or {})
        merged_headers, merged_params = self._apply_auth(merged_headers, merged_params)

        req_log = self._log.bind(
            method=method,
            path=path,
            params=_redact_params(merged_params),
            headers=_redact_headers(merged_headers),
        )

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential_jitter(initial=0.5, max=8),
            retry=retry_if_exception_type(_RETRYABLE),
            reraise=True,
        ):
            with attempt:
                await self._bucket.acquire()
                start = time.perf_counter()
                resp = await self._client.request(
                    method, path, params=merged_params, json=json, headers=merged_headers
                )
                duration_ms = int((time.perf_counter() - start) * 1000)

                self.cost.add_request()
                req_log.info(
                    "http_request",
                    status=resp.status_code,
                    duration_ms=duration_ms,
                    bytes=len(resp.content or b""),
                    attempt=attempt.retry_state.attempt_number,
                )

                if resp.status_code == 429:
                    raise RateLimitedError(f"{self.provider} rate-limited: {resp.text[:200]}")
                if resp.status_code >= 500:
                    raise UpstreamError(f"{self.provider} {resp.status_code}: {resp.text[:200]}")
                if resp.status_code >= 400:
                    raise ProviderError(f"{self.provider} {resp.status_code}: {resp.text[:200]}")
                return resp

        raise RuntimeError("unreachable")  # pragma: no cover

    async def get_json(self, path: str, **kwargs: Any) -> Any:
        resp = await self.request("GET", path, **kwargs)
        return resp.json()
