"""In-process event broker.

Shape:
- Detectors / ingestion jobs call `fii_db.emit_event(...)` (re-exported below) which
  persists a RefreshEvent row and fires `pg_notify('fii_events', event_id)`.
- The Postgres LISTEN task in `app.listener` loads the row, applies watchlist gating,
  and publishes a decorated envelope to this `EventBroker`.
- SSE endpoints + quick_refresh trigger subscribe to the broker.

Single-process today (one API replica). Redis pub/sub slot opens when we scale out —
the public surface (`EventBroker.publish/subscribe`, `emit_event`) stays the same.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog

# Re-export so route modules have a single import location.
from fii_db import DEBOUNCE_WINDOW, PG_CHANNEL, emit_event  # noqa: F401

log = structlog.get_logger(__name__)


class EventBroker:
    """Fan-out to in-process async subscribers. Queue-per-subscriber."""

    def __init__(self, max_queue_size: int = 256) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._max_queue_size = max_queue_size

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow subscriber — drop the oldest so the UI stays current.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning("broker_subscriber_dropped")

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
