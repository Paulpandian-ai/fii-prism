"""Events API.

- GET /events                → recent refresh events
- GET /events/stream         → SSE live broker fan-out
- POST /events/simulate      → dev helper to inject an event (demos, tests)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fii_db import EventType, RefreshEvent
from fii_db.session import session_scope
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.agents_runtime import AgentsRuntime, get_runtime
from app.event_runtime import get_broker
from app.events import emit_event

log = structlog.get_logger(__name__)

# ruff: noqa: B008
router = APIRouter(prefix="/events", tags=["events"])


class EventItem(BaseModel):
    event_id: str
    symbol: str
    event_type: str
    payload: dict[str, Any]
    detected_at: datetime
    processed_at: datetime | None = None
    analysis_id: str | None = None


class SimulateEventRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    event_type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("", response_model=list[EventItem])
async def list_events(
    symbol: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=50, ge=1, le=200),
    runtime: AgentsRuntime = Depends(get_runtime),
) -> list[EventItem]:
    with session_scope(runtime.session_factory) as s:
        stmt = select(RefreshEvent).order_by(desc(RefreshEvent.detected_at)).limit(limit)
        if symbol:
            stmt = stmt.where(RefreshEvent.symbol == symbol.upper())
        rows = s.execute(stmt).scalars().all()
    return [
        EventItem(
            event_id=str(r.event_id),
            symbol=r.symbol,
            event_type=r.event_type,
            payload=r.payload or {},
            detected_at=r.detected_at,
            processed_at=r.processed_at,
            analysis_id=str(r.analysis_id) if r.analysis_id else None,
        )
        for r in rows
    ]


@router.get("/stream")
async def stream_events() -> StreamingResponse:
    """SSE fan-out from the in-process broker. Each event_envelope becomes one data: line."""
    broker = get_broker()

    async def event_source() -> AsyncIterator[bytes]:
        q = await broker.subscribe()
        try:
            yield _sse({"kind": "_open"})
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield _sse(evt)
                except TimeoutError:
                    # Heartbeat keeps proxies / CDNs from closing the stream.
                    yield b": ping\n\n"
        finally:
            await broker.unsubscribe(q)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/simulate", status_code=202)
async def simulate_event(
    req: SimulateEventRequest,
    runtime: AgentsRuntime = Depends(get_runtime),
) -> dict[str, str | None]:
    """Dev-only helper to drop a synthetic event onto the channel. Handy for demos /
    integration tests — the same code path as real detectors.
    """
    try:
        event_id = emit_event(
            runtime.session_factory,
            symbol=req.symbol,
            event_type=req.event_type,
            payload=req.payload,
        )
    except Exception as exc:
        log.exception("simulate_event_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"event_id": event_id}


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode()
