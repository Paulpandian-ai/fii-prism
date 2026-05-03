"""FRED macro ingestion."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from fii_data_clients import DEFAULT_MACRO_SERIES, FredClient
from fii_db import MacroSeries
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fii_ingest.bulk import chunked_upsert

log = structlog.get_logger(__name__)


def _d(v: Any) -> Decimal | None:
    if v in (None, "", "."):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


async def ingest_series(session: Session, *, fred: FredClient, series_id: str) -> int:
    obs = await fred.get_series(series_id)
    rows = []
    for o in obs:
        d = _to_date(o.get("date"))
        if d is None:
            continue
        rows.append(
            {
                "series_id": series_id,
                "observation_date": d,
                "value": _d(o.get("value")),
                "release_id": None,
            }
        )
    if not rows:
        return 0

    def _build(chunk: list[dict[str, Any]]):
        stmt = pg_insert(MacroSeries).values(chunk)
        return stmt.on_conflict_do_update(
            index_elements=[MacroSeries.series_id, MacroSeries.observation_date],
            set_={"value": stmt.excluded.value},
        )

    chunked_upsert(session, rows, build_stmt=_build, label=f"macro_series:{series_id}")
    log.info("macro_series_upserted", series=series_id, rows=len(rows))
    return len(rows)


async def ingest_all_default(session: Session, *, fred: FredClient) -> int:
    total = 0
    for series_id in DEFAULT_MACRO_SERIES:
        total += await ingest_series(session, fred=fred, series_id=series_id)
    return total


def _to_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None
