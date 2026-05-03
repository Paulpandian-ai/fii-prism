"""FRED macro ingestion.

Each FRED series is ingested in its own transaction. A failure on one series
(HTTP 400 from a discontinued series like GOLDAMGBD228NLBM, a rate-limit, a
parse error) rolls back ONLY that series — every series that succeeded before
or after stays committed. The previous all-or-nothing transaction caused a
93K-row macro corpus to be lost when a single discontinued series 400'd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from fii_data_clients import DEFAULT_MACRO_SERIES, FredClient
from fii_db import MacroSeries
from fii_db.session import session_scope
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from fii_ingest.bulk import chunked_upsert

log = structlog.get_logger(__name__)


@dataclass
class MacroIngestReport:
    """Result of a multi-series macro ingest. `total_rows` is the sum across
    series that committed successfully; `failed` is one entry per series that
    raised, in (series_id, error) form."""

    total_rows: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _d(v: Any) -> Decimal | None:
    if v in (None, "", "."):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


async def ingest_series(session: Session, *, fred: FredClient, series_id: str) -> int:
    """Ingest one FRED series into the supplied session. Caller owns the
    transaction; this is the single-series helper used by tests and by
    ingest_all_default below."""
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


async def ingest_all_default(
    factory: sessionmaker,
    *,
    fred: FredClient,
    series_ids: tuple[str, ...] = DEFAULT_MACRO_SERIES,
) -> MacroIngestReport:
    """Ingest the default series catalog with per-series transaction isolation.

    Each series is wrapped in its own ``session_scope`` so an HTTP 400 / parse
    error / DB constraint violation on one series does not roll back the rest.
    Returns a ``MacroIngestReport`` with the total committed row count and the
    full list of per-series failures.
    """
    report = MacroIngestReport()
    for series_id in series_ids:
        try:
            with session_scope(factory) as s:
                rows = await ingest_series(s, fred=fred, series_id=series_id)
            report.total_rows += rows
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:300]
            log.warning(
                "macro_series_failed_continuing",
                series=series_id,
                error=err,
            )
            report.failed.append((series_id, err))
    log.info(
        "macro_ingest_complete",
        total_rows=report.total_rows,
        succeeded=len(series_ids) - len(report.failed),
        failed=len(report.failed),
        failed_series=[s for s, _ in report.failed],
    )
    return report


def _to_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None
