"""Tools for the Insider Flow specialist."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import structlog
from fii_db import InsiderTransaction, InstitutionalHolding
from fii_db.session import session_scope
from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

log = structlog.get_logger(__name__)


@dataclass
class InsiderToolContext:
    factory: sessionmaker
    call_count: int = 0
    max_calls: int = 8


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_form4_transactions",
        "description": "Form 4 insider transactions in the trailing N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "since_days": {"type": "integer", "minimum": 1, "maximum": 720, "default": 180},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_13f_holdings",
        "description": "Latest quarterly 13F holdings snapshot for the symbol.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_13f_changes",
        "description": "QoQ change in institutional ownership over the last N quarters.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "quarters": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "detect_cluster_activity",
        "description": (
            "Deterministic cluster detection over the supplied transactions. Returns flags "
            "for cluster_buying / cluster_selling and the unique-insider count in the window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "window_days": {"type": "integer", "minimum": 7, "maximum": 90, "default": 30},
                "min_unique_insiders": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 10,
                    "default": 3,
                },
            },
            "required": ["symbol"],
        },
    },
]


class ToolCallError(Exception):
    pass


async def dispatch(name: str, input_: dict[str, Any], ctx: InsiderToolContext) -> dict[str, Any]:
    ctx.call_count += 1
    if ctx.call_count > ctx.max_calls:
        raise ToolCallError(f"Tool-call budget exhausted ({ctx.max_calls})")
    start = time.perf_counter()
    success = True
    try:
        return await _route(name, input_, ctx)
    except Exception as exc:
        success = False
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        log.info(
            "tool_call",
            tool=name,
            specialist="insider",
            symbol=input_.get("symbol"),
            call_number=ctx.call_count,
            duration_ms=int((time.perf_counter() - start) * 1000),
            success=success,
        )


async def _route(name: str, input_: dict[str, Any], ctx: InsiderToolContext) -> dict[str, Any]:
    if name == "get_form4_transactions":
        return _get_form4(ctx, input_["symbol"], int(input_.get("since_days") or 180))
    if name == "get_13f_holdings":
        return _get_13f_latest(ctx, input_["symbol"])
    if name == "get_13f_changes":
        return _get_13f_changes(ctx, input_["symbol"], int(input_.get("quarters") or 4))
    if name == "detect_cluster_activity":
        return _detect_cluster(
            ctx,
            input_["symbol"],
            int(input_.get("window_days") or 30),
            int(input_.get("min_unique_insiders") or 3),
        )
    raise ToolCallError(f"unknown tool: {name}")


def _get_form4(ctx: InsiderToolContext, symbol: str, since_days: int) -> dict[str, Any]:
    cutoff = date.today() - timedelta(days=since_days)
    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(InsiderTransaction)
                .where(
                    InsiderTransaction.symbol == symbol.upper(),
                    InsiderTransaction.transaction_date >= cutoff,
                )
                .order_by(desc(InsiderTransaction.transaction_date))
            )
            .scalars()
            .all()
        )
    items = [
        {
            "id": str(r.id),
            "insider_name": r.insider_name,
            "role": r.role,
            "transaction_type": r.transaction_type,
            "shares": float(r.shares) if r.shares is not None else None,
            "price": float(r.price) if r.price is not None else None,
            "transaction_date": r.transaction_date.isoformat(),
            "filed_at": r.filed_at.isoformat() if r.filed_at else None,
        }
        for r in rows
    ]
    net_dollars_90 = 0.0
    cutoff_90 = date.today() - timedelta(days=90)
    for it in items:
        if it["price"] is None or it["shares"] is None:
            continue
        if date.fromisoformat(it["transaction_date"]) < cutoff_90:
            continue
        sign = 1.0 if (it["transaction_type"] or "").upper() in {"P", "A", "M"} else -1.0
        net_dollars_90 += sign * it["price"] * it["shares"]
    return {
        "symbol": symbol.upper(),
        "since_days": since_days,
        "count": len(items),
        "transactions": items,
        "net_dollars_trailing_90d": net_dollars_90,
    }


def _get_13f_latest(ctx: InsiderToolContext, symbol: str) -> dict[str, Any]:
    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(InstitutionalHolding)
                .where(InstitutionalHolding.symbol == symbol.upper())
                .order_by(desc(InstitutionalHolding.report_period_end))
                .limit(50)
            )
            .scalars()
            .all()
        )
    if not rows:
        return {
            "symbol": symbol.upper(),
            "count": 0,
            "note": "no 13F rows ingested; ingestion is not wired in MVP",
        }
    latest_period = rows[0].report_period_end
    latest = [r for r in rows if r.report_period_end == latest_period]
    return {
        "symbol": symbol.upper(),
        "report_period_end": latest_period.isoformat(),
        "count": len(latest),
        "holders": [
            {
                "holder_cik": r.holder_cik,
                "holder_name": r.holder_name,
                "shares": float(r.shares) if r.shares is not None else None,
                "value_usd": float(r.value_usd) if r.value_usd is not None else None,
            }
            for r in latest
        ],
    }


def _get_13f_changes(ctx: InsiderToolContext, symbol: str, quarters: int) -> dict[str, Any]:
    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(InstitutionalHolding)
                .where(InstitutionalHolding.symbol == symbol.upper())
                .order_by(desc(InstitutionalHolding.report_period_end))
            )
            .scalars()
            .all()
        )
    if not rows:
        return {
            "symbol": symbol.upper(),
            "count": 0,
            "note": "no 13F rows ingested; ingestion is not wired in MVP",
        }
    by_period: dict[str, float] = defaultdict(float)
    for r in rows:
        by_period[r.report_period_end.isoformat()] += float(r.shares or 0)
    series = sorted(by_period.items(), reverse=True)[:quarters]
    deltas = []
    for i, (period, total) in enumerate(series):
        prev = series[i + 1][1] if i + 1 < len(series) else None
        delta_pct = None
        if prev and prev > 0:
            delta_pct = (total - prev) / prev
        deltas.append({"period": period, "total_shares": total, "qoq_change_pct": delta_pct})
    return {"symbol": symbol.upper(), "deltas": deltas}


def _detect_cluster(
    ctx: InsiderToolContext, symbol: str, window_days: int, min_unique_insiders: int
) -> dict[str, Any]:
    """Cluster: at least `min_unique_insiders` distinct insiders trading in the same direction
    within `window_days`. Returns separate buy/sell flags."""
    cutoff = date.today() - timedelta(days=window_days)
    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(InsiderTransaction).where(
                    InsiderTransaction.symbol == symbol.upper(),
                    InsiderTransaction.transaction_date >= cutoff,
                )
            )
            .scalars()
            .all()
        )
    buyers: set[str] = set()
    sellers: set[str] = set()
    for r in rows:
        code = (r.transaction_type or "").upper()
        if code in {"P", "A", "M"}:
            buyers.add(r.insider_name)
        elif code in {"S", "F"}:
            sellers.add(r.insider_name)
    return {
        "symbol": symbol.upper(),
        "window_days": window_days,
        "min_unique_insiders": min_unique_insiders,
        "unique_buyers": len(buyers),
        "unique_sellers": len(sellers),
        "cluster_buying": len(buyers) >= min_unique_insiders,
        "cluster_selling": len(sellers) >= min_unique_insiders,
    }
