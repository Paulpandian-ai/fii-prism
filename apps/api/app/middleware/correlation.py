"""Correlation-ID middleware (pure ASGI to avoid Starlette's BaseHTTPMiddleware
deadlocks under TestClient).

Reads X-Correlation-ID off the inbound request (or generates one), exposes it as a
contextvar so structlog can include it in every log line emitted during the request,
and echoes it back on the response. Specialists pick up the ID via the same contextvar
when they log inside async tasks spawned from the request.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

import structlog

CORRELATION_ID_VAR: ContextVar[str | None] = ContextVar("correlation_id", default=None)
HEADER = b"x-correlation-id"


def get_correlation_id() -> str | None:
    return CORRELATION_ID_VAR.get()


def set_correlation_id(value: str | None) -> None:
    CORRELATION_ID_VAR.set(value)


class CorrelationIdMiddleware:
    """Pure ASGI middleware. Compatible with Starlette's TestClient under sync tests."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cid = self._extract_or_generate(scope)
        token = CORRELATION_ID_VAR.set(cid)
        structlog.contextvars.bind_contextvars(correlation_id=cid)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER, cid.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
            CORRELATION_ID_VAR.reset(token)

    @staticmethod
    def _extract_or_generate(scope) -> str:
        for name, value in scope.get("headers", []):
            if name == HEADER:
                try:
                    return value.decode()
                except UnicodeDecodeError:
                    break
        return str(uuid.uuid4())
