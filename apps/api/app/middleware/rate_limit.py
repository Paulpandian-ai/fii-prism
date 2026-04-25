"""In-process token-bucket rate limiter per client IP (pure ASGI middleware).

Production should rely on API Gateway throttling for ingress (configured in CDK);
this is a defensive layer for local dev and the early single-replica ECS rollout
when no gateway sits in front. Bucket state is per-process (best effort across
multi-replica deployments).
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict

import structlog

log = structlog.get_logger(__name__)


class _Bucket:
    __slots__ = ("last", "tokens")

    def __init__(self, capacity: float) -> None:
        self.tokens = capacity
        self.last = time.monotonic()


class RateLimitMiddleware:
    """Per-IP token bucket. Defaults: 100 req/min, burst 100.

    Streaming endpoints (SSE) and read-only health checks are exempted to avoid
    breaking long-lived connections or k8s liveness probes.
    """

    def __init__(
        self,
        app,
        *,
        per_minute: int = 100,
        burst: int | None = None,
        exempt_prefixes: tuple[str, ...] = ("/health", "/ready"),
    ) -> None:
        self.app = app
        self.rate_per_sec = per_minute / 60.0
        self.capacity = float(burst if burst is not None else per_minute)
        self.exempt_prefixes = exempt_prefixes
        self._buckets: dict[str, _Bucket] = defaultdict(lambda: _Bucket(self.capacity))
        # threading.Lock (not asyncio.Lock) so the middleware instance can outlive a
        # single TestClient lifespan without binding to a stale event loop. The locked
        # section is microsecond-scale arithmetic, so we don't yield to the loop here.
        self._lock = threading.Lock()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path.startswith(p) for p in self.exempt_prefixes):
            await self.app(scope, receive, send)
            return

        ip = self._client_ip(scope)
        if not await self._take_token(ip):
            log.warning("rate_limited", ip=ip, path=path)
            await self._send_429(send)
            return
        await self.app(scope, receive, send)

    async def _take_token(self, ip: str) -> bool:
        with self._lock:
            bucket = self._buckets[ip]
            now = time.monotonic()
            bucket.tokens = min(
                self.capacity, bucket.tokens + (now - bucket.last) * self.rate_per_sec
            )
            bucket.last = now
            if bucket.tokens < 1.0:
                return False
            bucket.tokens -= 1.0
            return True

    @staticmethod
    def _client_ip(scope) -> str:
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                try:
                    return value.decode().split(",", 1)[0].strip()
                except UnicodeDecodeError:
                    break
        client = scope.get("client")
        return client[0] if client else "anonymous"

    @staticmethod
    async def _send_429(send) -> None:
        body = json.dumps({"detail": "rate limit exceeded — try again in a few seconds"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"5"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
