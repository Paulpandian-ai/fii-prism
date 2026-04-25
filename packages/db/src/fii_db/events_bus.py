"""Event emission + debounce. Shared across API (listener) and ingest (detectors).

Writes a RefreshEvent row inside a SQLAlchemy session and pg_notify's the channel.
The 15-minute debounce collapses repeated identical events from the same detector
into a single row.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, select, text
from sqlalchemy.orm import sessionmaker

from fii_db.enums import EventType
from fii_db.models import RefreshEvent
from fii_db.session import session_scope

log = structlog.get_logger(__name__)


PG_CHANNEL = "fii_events"
DEBOUNCE_WINDOW = timedelta(minutes=15)


def _dedupe_key(symbol: str, event_type: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{symbol}|{event_type}|{body}".encode()).hexdigest()[:32]
    return f"{symbol}:{event_type}:{digest}"


def emit_event(
    factory: sessionmaker,
    *,
    symbol: str,
    event_type: EventType,
    payload: dict[str, Any] | None = None,
) -> str | None:
    """Persist a RefreshEvent and NOTIFY the `fii_events` channel.

    Returns the event_id (str), or None if debounced within the 15-minute window.
    """
    symbol = symbol.upper()
    payload = payload or {}
    et_value = event_type.value if isinstance(event_type, EventType) else str(event_type)
    key = _dedupe_key(symbol, et_value, payload)

    cutoff = datetime.now(UTC) - DEBOUNCE_WINDOW
    with session_scope(factory) as s:
        existing = s.execute(
            select(RefreshEvent.event_id).where(
                and_(
                    RefreshEvent.symbol == symbol,
                    RefreshEvent.event_type == et_value,
                    RefreshEvent.dedupe_key == key,
                    RefreshEvent.detected_at >= cutoff,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            log.info(
                "event_debounced",
                symbol=symbol,
                event_type=et_value,
                existing_id=str(existing),
            )
            return None

        row = RefreshEvent(
            symbol=symbol,
            event_type=et_value,
            payload=payload,
            dedupe_key=key,
        )
        s.add(row)
        s.flush()
        event_id = str(row.event_id)
        s.execute(
            text("SELECT pg_notify(:ch, :payload)").bindparams(ch=PG_CHANNEL, payload=event_id)
        )

    log.info("event_emitted", symbol=symbol, event_type=et_value, event_id=event_id)
    return event_id
