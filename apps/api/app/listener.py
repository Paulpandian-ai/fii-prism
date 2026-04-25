"""Postgres LISTEN task.

Runs one long-lived async psycopg connection. On NOTIFY fii_events, loads the
RefreshEvent row, checks single-user watchlist membership, broadcasts to the
in-process EventBroker, and (when watchlisted) kicks off a quick_refresh
analysis in the background.

The listener is resilient: if the connection drops it reconnects with backoff.
Process lifetime = API process lifetime.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import structlog
from fii_agents import run_analysis
from fii_db import Analysis, AnalysisStatus, AnalysisType, RefreshEvent, Watchlist
from fii_db.models import SINGLE_USER_ID
from fii_db.session import session_scope
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents_runtime import AgentsRuntime
from app.events import PG_CHANNEL, EventBroker

log = structlog.get_logger(__name__)

# Background quick_refresh tasks. Held here so the GC doesn't reap them.
_BACKGROUND: set[asyncio.Task] = set()


def _raw_psycopg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


async def run_listener(runtime: AgentsRuntime, broker: EventBroker) -> None:
    """Main listener loop. Reconnects forever on failure."""
    dsn = _raw_psycopg_dsn(runtime.database_url)
    backoff = 1.0
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as aconn:
                async with aconn.cursor() as acur:
                    await acur.execute(f"LISTEN {PG_CHANNEL}")
                log.info("listener_connected", channel=PG_CHANNEL)
                backoff = 1.0

                async for notify in aconn.notifies():
                    await _handle_notify(notify, runtime, broker)
        except asyncio.CancelledError:
            log.info("listener_cancelled")
            raise
        except Exception as exc:
            log.warning("listener_reconnecting", error=str(exc), backoff_s=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def _handle_notify(notify: Any, runtime: AgentsRuntime, broker: EventBroker) -> None:
    event_id = notify.payload.strip()
    if not event_id:
        return

    try:
        row = _load_event(runtime, event_id)
    except Exception:
        log.exception("event_load_failed", event_id=event_id)
        return
    if row is None:
        log.warning("notify_for_unknown_event", event_id=event_id)
        return

    envelope = {
        "event_id": str(row["event_id"]),
        "symbol": row["symbol"],
        "event_type": row["event_type"],
        "payload": row["payload"],
        "detected_at": row["detected_at"].isoformat() if row["detected_at"] else None,
        "watchlisted": row["watchlisted"],
    }

    # Always broadcast (UI shows feed for everyone; the "watchlisted" flag lets the UI
    # highlight actionable events).
    await broker.publish({"kind": "event", **envelope})

    if not row["watchlisted"]:
        return

    # Watchlisted: trigger a quick_refresh analysis in the background.
    analysis_id = str(uuid.uuid4())
    _precreate_quick_refresh_row(runtime, analysis_id=analysis_id, symbol=row["symbol"])
    _mark_event_dispatched(runtime, event_id=row["event_id"], analysis_id=analysis_id)

    task = asyncio.create_task(
        _run_quick_refresh(
            runtime=runtime,
            broker=broker,
            analysis_id=analysis_id,
            symbol=row["symbol"],
            event_type=row["event_type"],
            event_id=str(row["event_id"]),
        )
    )
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def _load_event(runtime: AgentsRuntime, event_id: str) -> dict[str, Any] | None:
    with session_scope(runtime.session_factory) as s:
        ev = s.execute(
            select(RefreshEvent).where(RefreshEvent.event_id == event_id)
        ).scalar_one_or_none()
        if ev is None:
            return None
        watch = s.execute(
            select(Watchlist.symbol).where(
                Watchlist.user_id == SINGLE_USER_ID,
                Watchlist.symbol == ev.symbol,
            )
        ).scalar_one_or_none()
        return {
            "event_id": ev.event_id,
            "symbol": ev.symbol,
            "event_type": ev.event_type,
            "payload": ev.payload,
            "detected_at": ev.detected_at,
            "watchlisted": watch is not None,
        }


def _precreate_quick_refresh_row(runtime: AgentsRuntime, *, analysis_id: str, symbol: str) -> None:
    with session_scope(runtime.session_factory) as s:
        stmt = pg_insert(Analysis).values(
            analysis_id=analysis_id,
            symbol=symbol,
            analysis_type=AnalysisType.QUICK_REFRESH.value,
            status=AnalysisStatus.PENDING.value,
            initiated_at=datetime.now(UTC),
            model_calls_json={},
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=[Analysis.analysis_id])
        s.execute(stmt)


def _mark_event_dispatched(runtime: AgentsRuntime, *, event_id: str, analysis_id: str) -> None:
    with session_scope(runtime.session_factory) as s:
        s.execute(
            update(RefreshEvent)
            .where(RefreshEvent.event_id == event_id)
            .values(analysis_id=analysis_id, processed_at=datetime.now(UTC))
        )


async def _run_quick_refresh(
    *,
    runtime: AgentsRuntime,
    broker: EventBroker,
    analysis_id: str,
    symbol: str,
    event_type: str,
    event_id: str,
) -> None:
    try:
        await run_analysis(
            symbol=symbol,
            analysis_id=analysis_id,
            user_id=SINGLE_USER_ID,
            database_url=runtime.database_url,
            factory=runtime.session_factory,
            embedder=runtime.embedder,
            raw_bucket=runtime.raw_bucket,
            budget=runtime.budget,
            extra_context={
                "quick_refresh_event_type": event_type,
                "quick_refresh_event_id": event_id,
                "model_tier": "haiku",
            },
        )
        await broker.publish(
            {
                "kind": "analysis_updated",
                "analysis_id": analysis_id,
                "symbol": symbol,
                "event_type": event_type,
                "event_id": event_id,
            }
        )
    except Exception:
        log.exception("quick_refresh_failed", analysis_id=analysis_id, symbol=symbol)


def start_listener_task(runtime: AgentsRuntime, broker: EventBroker) -> asyncio.Task:
    """Schedule the listener on the current event loop."""
    task = asyncio.create_task(run_listener(runtime, broker), name="fii-listener")
    return task


async def shutdown_listener(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
