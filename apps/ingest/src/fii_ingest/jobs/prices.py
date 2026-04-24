"""Daily and intraday price ingestion from Polygon."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fii_data_clients import PolygonClient
from fii_db import PriceDaily, PriceIntraday
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)


def _to_decimal(x: Any) -> Decimal | None:
    return Decimal(str(x)) if x is not None else None


async def ingest_daily(
    session: Session,
    *,
    polygon: PolygonClient,
    symbol: str,
    start: date,
    end: date | None = None,
) -> int:
    end = end or date.today()
    bars = await polygon.get_daily_bars(symbol, start, end)
    if not bars:
        log.info("no_daily_bars", symbol=symbol, start=str(start), end=str(end))
        return 0

    rows = []
    for b in bars:
        ts_ms = b.get("t")
        if ts_ms is None:
            continue
        d = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).date()
        rows.append(
            {
                "symbol": symbol.upper(),
                "trade_date": d,
                "open": _to_decimal(b.get("o")),
                "high": _to_decimal(b.get("h")),
                "low": _to_decimal(b.get("l")),
                "close": _to_decimal(b.get("c")),
                "adjusted_close": _to_decimal(b.get("c")),  # aggs/v2 returns adjusted by default
                "volume": b.get("v"),
                "vwap": _to_decimal(b.get("vw")),
                "source": "polygon",
            }
        )
    if not rows:
        return 0

    stmt = pg_insert(PriceDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[PriceDaily.symbol, PriceDaily.trade_date],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "adjusted_close": stmt.excluded.adjusted_close,
            "volume": stmt.excluded.volume,
            "vwap": stmt.excluded.vwap,
            "source": stmt.excluded.source,
        },
    )
    session.execute(stmt)
    log.info("daily_prices_upserted", symbol=symbol, rows=len(rows))
    return len(rows)


async def ingest_intraday(
    session: Session,
    *,
    polygon: PolygonClient,
    symbol: str,
    days_back: int = 7,
) -> int:
    end = date.today()
    start = end - timedelta(days=days_back)
    bars = await polygon.get_intraday_bars(symbol, start, end)
    if not bars:
        return 0

    rows = []
    for b in bars:
        ts_ms = b.get("t")
        if ts_ms is None:
            continue
        rows.append(
            {
                "symbol": symbol.upper(),
                "bar_ts": datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                "open": _to_decimal(b.get("o")),
                "high": _to_decimal(b.get("h")),
                "low": _to_decimal(b.get("l")),
                "close": _to_decimal(b.get("c")),
                "volume": b.get("v"),
                "vwap": _to_decimal(b.get("vw")),
                "source": "polygon",
            }
        )
    if not rows:
        return 0

    stmt = pg_insert(PriceIntraday).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=[PriceIntraday.symbol, PriceIntraday.bar_ts])
    session.execute(stmt)
    log.info("intraday_prices_upserted", symbol=symbol, rows=len(rows))
    return len(rows)
