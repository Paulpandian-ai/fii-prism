"""Watchlist CRUD. Single-user MVP: all rows use the SINGLE_USER_ID sentinel."""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fii_db import Ticker, Watchlist
from fii_db.models import SINGLE_USER_ID
from fii_db.session import session_scope
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents_runtime import AgentsRuntime, get_runtime

log = structlog.get_logger(__name__)

# ruff: noqa: B008  # Depends() in defaults is FastAPI's DI pattern.
router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistEntry(BaseModel):
    symbol: str
    added_at: datetime
    notes: str | None = None


class WatchlistUpsert(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    notes: str | None = None


@router.get("", response_model=list[WatchlistEntry])
async def list_watchlist(runtime: AgentsRuntime = Depends(get_runtime)) -> list[WatchlistEntry]:
    with session_scope(runtime.session_factory) as s:
        rows = (
            s.execute(
                select(Watchlist)
                .where(Watchlist.user_id == SINGLE_USER_ID)
                .order_by(Watchlist.added_at.desc())
            )
            .scalars()
            .all()
        )
    return [WatchlistEntry(symbol=r.symbol, added_at=r.added_at, notes=r.notes) for r in rows]


@router.put("", response_model=WatchlistEntry, status_code=200)
async def upsert_watchlist(
    req: WatchlistUpsert, runtime: AgentsRuntime = Depends(get_runtime)
) -> WatchlistEntry:
    symbol = req.symbol.upper()
    with session_scope(runtime.session_factory) as s:
        # Ensure the ticker FK is satisfied; minimal row is fine.
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )
        stmt = pg_insert(Watchlist).values(
            user_id=SINGLE_USER_ID,
            symbol=symbol,
            notes=req.notes,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Watchlist.user_id, Watchlist.symbol],
            set_={"notes": stmt.excluded.notes},
        )
        s.execute(stmt)
        row = s.execute(
            select(Watchlist).where(Watchlist.user_id == SINGLE_USER_ID, Watchlist.symbol == symbol)
        ).scalar_one()
        return WatchlistEntry(symbol=row.symbol, added_at=row.added_at, notes=row.notes)


@router.delete("/{symbol}", status_code=204)
async def remove_watchlist(symbol: str, runtime: AgentsRuntime = Depends(get_runtime)) -> None:
    symbol = symbol.upper()
    with session_scope(runtime.session_factory) as s:
        row = s.execute(
            select(Watchlist).where(Watchlist.user_id == SINGLE_USER_ID, Watchlist.symbol == symbol)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="not on watchlist")
        s.delete(row)
    return None
