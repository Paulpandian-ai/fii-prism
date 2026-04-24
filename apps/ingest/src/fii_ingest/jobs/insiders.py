"""Form 4 insider transaction ingestion via edgartools."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fii_data_clients import EdgarClient
from fii_db import InsiderTransaction
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)


def _d(v: Any) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


async def ingest_insiders(
    session: Session, *, edgar: EdgarClient, symbol: str, days_back: int = 365
) -> int:
    since = date.today() - timedelta(days=days_back)
    trades = await edgar.get_form4s(symbol, since=since)
    if not trades:
        return 0

    rows = []
    for t in trades:
        rows.append(
            {
                "symbol": symbol.upper(),
                "insider_name": t.insider_name,
                "role": t.role,
                "transaction_type": t.transaction_type,
                "shares": _d(t.shares),
                "price": _d(t.price),
                "transaction_date": t.transaction_date,
                "filed_at": t.filed_at,
                "accession_no": t.accession_no,
            }
        )

    stmt = pg_insert(InsiderTransaction).values(rows)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_insider_dedupe")
    session.execute(stmt)
    log.info("insiders_upserted", symbol=symbol, rows=len(rows))
    return len(rows)
