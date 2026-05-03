"""Fundamentals ingestion from FMP.

Pulls the four statement types (income, balance, cashflow, ratios) and upserts one row
per (symbol, fiscal_period_end, statement_type).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from fii_data_clients import FMPClient
from fii_db import FundamentalsQuarterly, StatementType
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fii_ingest.bulk import chunked_upsert

log = structlog.get_logger(__name__)


# Hot-column extraction rules per statement type. FMP's field names are stable.
_INCOME_FIELDS = {
    "revenue": "revenue",
    "gross_profit": "grossProfit",
    "operating_income": "operatingIncome",
    "net_income": "netIncome",
    "eps_diluted": "epsdiluted",
    "shares_outstanding": "weightedAverageShsOutDil",
}
_BALANCE_FIELDS = {
    "total_assets": "totalAssets",
    "total_liabilities": "totalLiabilities",
    "total_equity": "totalStockholdersEquity",
    "total_debt": "totalDebt",
    "cash_and_equivalents": "cashAndCashEquivalents",
}
_CASHFLOW_FIELDS = {
    "operating_cash_flow": "operatingCashFlow",
    "free_cash_flow": "freeCashFlow",
    "capex": "capitalExpenditure",
}
_RATIOS_FIELDS = {
    "gross_margin": "grossProfitMargin",
    "operating_margin": "operatingProfitMargin",
    "net_margin": "netProfitMargin",
    "return_on_equity": "returnOnEquity",
    "return_on_invested_capital": "returnOnCapitalEmployed",
    "debt_to_equity": "debtEquityRatio",
}


def _d(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


def _to_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _build_row(
    symbol: str, statement_type: StatementType, raw: dict[str, Any], fields: dict[str, str]
) -> dict[str, Any] | None:
    period_end = _to_date(raw.get("date") or raw.get("fillingDate") or raw.get("fiscalDateEnding"))
    if period_end is None:
        return None
    row: dict[str, Any] = {
        "symbol": symbol.upper(),
        "fiscal_period_end": period_end,
        "statement_type": statement_type.value,
        "currency": raw.get("reportedCurrency"),
        "fiscal_year": _int(raw.get("calendarYear")),
        "fiscal_quarter": _quarter(raw.get("period")),
        "period_length_days": None,
        "raw": raw,
        "source": "fmp",
    }
    for col, src in fields.items():
        row[col] = _d(raw.get(src))
    return row


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() else None
    except (ValueError, TypeError):
        return None


def _quarter(period: Any) -> int | None:
    if not period:
        return None
    s = str(period).upper()
    if s in {"Q1", "Q2", "Q3", "Q4"}:
        return int(s[1])
    if s == "FY":
        return 4
    return None


async def ingest_fundamentals(session: Session, *, fmp: FMPClient, symbol: str) -> int:
    """Ingest the four FMP statement types. Each fetcher is wrapped so one failure
    (HTTP 402 plan-gating, network blip, malformed payload) doesn't abort the whole
    seed — we log + skip + continue with the next statement type."""
    total = 0
    for stmt_type, fetcher, fields in (
        (StatementType.INCOME, fmp.get_income_statement, _INCOME_FIELDS),
        (StatementType.BALANCE, fmp.get_balance_sheet, _BALANCE_FIELDS),
        (StatementType.CASHFLOW, fmp.get_cash_flow, _CASHFLOW_FIELDS),
        (StatementType.RATIOS, fmp.get_ratios, _RATIOS_FIELDS),
    ):
        try:
            payload = await fetcher(symbol)
        except Exception as exc:
            log.warning(
                "fundamentals_fetch_failed",
                symbol=symbol,
                statement=stmt_type.value,
                error=str(exc)[:200],
            )
            continue
        if not payload:
            log.info(
                "fundamentals_empty_payload",
                symbol=symbol,
                statement=stmt_type.value,
            )
            continue
        rows = [r for r in (_build_row(symbol, stmt_type, p, fields) for p in payload) if r]
        if not rows:
            log.info(
                "fundamentals_no_rows_after_parse",
                symbol=symbol,
                statement=stmt_type.value,
            )
            continue
        update_cols = [
            c for c in rows[0] if c not in ("symbol", "fiscal_period_end", "statement_type")
        ]

        def _build(chunk: list[dict[str, Any]], _cols: list[str] = update_cols):
            stmt = pg_insert(FundamentalsQuarterly).values(chunk)
            return stmt.on_conflict_do_update(
                index_elements=[
                    FundamentalsQuarterly.symbol,
                    FundamentalsQuarterly.fiscal_period_end,
                    FundamentalsQuarterly.statement_type,
                ],
                set_={col: getattr(stmt.excluded, col) for col in _cols},
            )

        try:
            chunked_upsert(
                session,
                rows,
                build_stmt=_build,
                label=f"fundamentals_quarterly:{symbol}:{stmt_type.value}",
            )
        except Exception:
            log.exception("fundamentals_upsert_failed", symbol=symbol, statement=stmt_type.value)
            continue
        total += len(rows)
        log.info("fundamentals_upserted", symbol=symbol, statement=stmt_type.value, rows=len(rows))
    return total
