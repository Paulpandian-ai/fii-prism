"""Section 7: emit_event debounce + watchlist scoping (covers the listener gate)."""

from __future__ import annotations

import asyncio

import pytest
from fii_db import EventType, RefreshEvent, Ticker, Watchlist, emit_event
from fii_db.models import SINGLE_USER_ID
from fii_db.session import session_scope
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert


@pytest.fixture
def runtime():
    from app.agents_runtime import build_runtime

    return asyncio.get_event_loop().run_until_complete(build_runtime())


def test_emit_event_debounces_within_window(runtime):
    symbol = "ZZDEBOUNCE"
    payload = {"z": -4.1, "pct_move": -8.5, "direction": "down", "simulated": True}
    with session_scope(runtime.session_factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )
        s.execute(delete(RefreshEvent).where(RefreshEvent.symbol == symbol))

    first = emit_event(
        runtime.session_factory,
        symbol=symbol,
        event_type=EventType.PRICE_SHOCK,
        payload=payload,
    )
    second = emit_event(
        runtime.session_factory,
        symbol=symbol,
        event_type=EventType.PRICE_SHOCK,
        payload=payload,
    )
    assert first is not None
    assert second is None, "identical payload within window should debounce"

    # Different payload should NOT debounce.
    third = emit_event(
        runtime.session_factory,
        symbol=symbol,
        event_type=EventType.PRICE_SHOCK,
        payload={**payload, "z": -5.0},
    )
    assert third is not None

    with session_scope(runtime.session_factory) as s:
        s.execute(delete(RefreshEvent).where(RefreshEvent.symbol == symbol))


def test_watchlist_gating_computed_correctly(runtime):
    """The listener's on-NOTIFY watchlist check is what gates quick_refresh. We verify
    the lookup directly (the LISTEN path itself runs in a background task and requires
    a running API process; covered separately by the manual simulate-shock smoke test).
    """
    from app.listener import _load_event

    symbol_watched = "ZZWATCH"
    symbol_unwatched = "ZZUNWATCH"
    with session_scope(runtime.session_factory) as s:
        for sym in (symbol_watched, symbol_unwatched):
            s.execute(
                pg_insert(Ticker)
                .values(symbol=sym, name=sym)
                .on_conflict_do_nothing(index_elements=[Ticker.symbol])
            )
            s.execute(delete(RefreshEvent).where(RefreshEvent.symbol == sym))
        s.execute(
            delete(Watchlist)
            .where(Watchlist.user_id == SINGLE_USER_ID)
            .where(Watchlist.symbol.in_([symbol_watched, symbol_unwatched]))
        )
        s.execute(pg_insert(Watchlist).values(user_id=SINGLE_USER_ID, symbol=symbol_watched))

    id_watched = emit_event(
        runtime.session_factory,
        symbol=symbol_watched,
        event_type=EventType.PRICE_SHOCK,
        payload={"z": -3.5, "simulated": True},
    )
    id_unwatched = emit_event(
        runtime.session_factory,
        symbol=symbol_unwatched,
        event_type=EventType.PRICE_SHOCK,
        payload={"z": -3.5, "simulated": True},
    )
    assert id_watched and id_unwatched

    r1 = _load_event(runtime, id_watched)
    r2 = _load_event(runtime, id_unwatched)
    assert r1 is not None and r1["watchlisted"] is True
    assert r2 is not None and r2["watchlisted"] is False

    with session_scope(runtime.session_factory) as s:
        s.execute(
            delete(RefreshEvent).where(RefreshEvent.symbol.in_([symbol_watched, symbol_unwatched]))
        )
        s.execute(
            delete(Watchlist).where(
                Watchlist.user_id == SINGLE_USER_ID,
                Watchlist.symbol == symbol_watched,
            )
        )
