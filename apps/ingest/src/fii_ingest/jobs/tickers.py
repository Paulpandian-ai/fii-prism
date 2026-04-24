"""Upsert the tickers master row. Called by every per-ticker ingestion job."""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from fii_db import MarketCapBucket, Ticker
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)


def _bucket_from_market_cap(mcap: float | None) -> MarketCapBucket | None:
    if mcap is None:
        return None
    if mcap >= 200_000_000_000:
        return MarketCapBucket.MEGA
    if mcap >= 10_000_000_000:
        return MarketCapBucket.LARGE
    if mcap >= 2_000_000_000:
        return MarketCapBucket.MID
    return MarketCapBucket.SMALL


def upsert_from_fmp_profile(session: Session, symbol: str, profile: dict[str, Any] | None) -> None:
    """Upsert a ticker using an FMP /profile payload. Profile may be None for bootstrap."""
    values: dict[str, Any] = {
        "symbol": symbol.upper(),
        "name": (profile or {}).get("companyName") or symbol.upper(),
        "sector": (profile or {}).get("sector"),
        "industry": (profile or {}).get("industry"),
        "market_cap_bucket": _bucket_from_market_cap((profile or {}).get("mktCap")),
        "cik": (profile or {}).get("cik"),
        "is_active": True,
        "first_tracked_at": date.today(),
    }
    stmt = pg_insert(Ticker).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Ticker.symbol],
        set_={
            "name": stmt.excluded.name,
            "sector": stmt.excluded.sector,
            "industry": stmt.excluded.industry,
            "market_cap_bucket": stmt.excluded.market_cap_bucket,
            "cik": stmt.excluded.cik,
        },
    )
    session.execute(stmt)
    log.info("ticker_upserted", symbol=symbol)
