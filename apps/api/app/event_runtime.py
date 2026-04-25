"""Per-process singleton for the in-process EventBroker.

Kept separate from agents_runtime so event-only routes (watchlist, events) can
depend on the broker without forcing a hard dep on the agent runtime in tests.
"""

from __future__ import annotations

from app.events import EventBroker

_BROKER: EventBroker | None = None


def set_broker(broker: EventBroker) -> None:
    global _BROKER
    _BROKER = broker


def get_broker() -> EventBroker:
    if _BROKER is None:
        raise RuntimeError("EventBroker not initialized; startup did not run.")
    return _BROKER
