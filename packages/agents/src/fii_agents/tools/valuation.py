"""Tools for the Valuation specialist.

The agent proposes assumptions; we compute. Tools here are the only way for the
agent to obtain numeric values it can cite — the run_dcf and calculate_wacc
helpers are deterministic, the rest are read-throughs against Postgres.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog
from fii_db import FundamentalsQuarterly, StatementType
from fii_db.session import session_scope
from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from fii_agents.tools.shared import (
    DEFAULT_EQUITY_RISK_PREMIUM,
    calculate_beta,
    get_latest_macro_value,
)
from fii_agents.tools.shared import (
    calculate_wacc as _calculate_wacc,
)
from fii_agents.tools.shared import (
    run_dcf as _run_dcf,
)

log = structlog.get_logger(__name__)


@dataclass
class ValuationToolContext:
    factory: sessionmaker
    call_count: int = 0
    max_calls: int = 12


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_ratios",
        "description": "Recent quarterly ratio rows (gross/op/net margin, ROIC, debt/equity, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "periods": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_latest_balance_sheet",
        "description": "Most recent balance sheet (for debt and shares outstanding).",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_risk_free_rate_latest",
        "description": "Latest 10-year Treasury yield (FRED DGS10), as a decimal ratio.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_beta",
        "description": (
            "3-year daily-returns beta vs SPY. Returns null if price history is too thin."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "calculate_wacc",
        "description": (
            "CAPM cost of equity (rf + beta * ERP) with optional weighted cost of debt. "
            "Default equity risk premium is 5.5%."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "risk_free_rate": {"type": "number"},
                "beta": {"type": "number"},
                "equity_risk_premium": {"type": "number", "default": DEFAULT_EQUITY_RISK_PREMIUM},
                "cost_of_debt_after_tax": {"type": "number"},
                "debt_to_equity": {"type": "number", "default": 0.0},
            },
            "required": ["risk_free_rate", "beta"],
        },
    },
    {
        "name": "run_dcf",
        "description": (
            "Two-stage DCF. You provide assumptions (base_revenue, revenue_growth list, "
            "operating_margin, tax_rate, capex/da/nwc as % of revenue, wacc, terminal_growth, "
            "shares_outstanding, net_debt). Returns intrinsic value per share and the "
            "year-by-year projection. NEVER do this math yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base_revenue": {"type": "number"},
                "revenue_growth": {"type": "array", "items": {"type": "number"}, "minItems": 1},
                "operating_margin": {"type": "number"},
                "tax_rate": {"type": "number", "default": 0.21},
                "capex_pct_of_revenue": {"type": "number"},
                "da_pct_of_revenue": {"type": "number"},
                "nwc_pct_of_revenue": {"type": "number", "default": 0.0},
                "wacc": {"type": "number"},
                "terminal_growth": {"type": "number"},
                "shares_outstanding": {"type": "number"},
                "net_debt": {"type": "number"},
            },
            "required": [
                "base_revenue",
                "revenue_growth",
                "operating_margin",
                "capex_pct_of_revenue",
                "da_pct_of_revenue",
                "wacc",
                "terminal_growth",
                "shares_outstanding",
                "net_debt",
            ],
        },
    },
]


class ToolCallError(Exception):
    pass


async def dispatch(name: str, input_: dict[str, Any], ctx: ValuationToolContext) -> dict[str, Any]:
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
            specialist="valuation",
            symbol=input_.get("symbol"),
            call_number=ctx.call_count,
            duration_ms=int((time.perf_counter() - start) * 1000),
            success=success,
        )


async def _route(name: str, input_: dict[str, Any], ctx: ValuationToolContext) -> dict[str, Any]:
    if name == "get_ratios":
        return _get_ratios(ctx, input_["symbol"], int(input_.get("periods") or 8))
    if name == "get_latest_balance_sheet":
        return _get_latest_balance_sheet(ctx, input_["symbol"])
    if name == "get_risk_free_rate_latest":
        return get_latest_macro_value(ctx.factory, "DGS10") | {
            "as_ratio_note": "value is in percent; divide by 100 for the ratio used in calculate_wacc"
        }
    if name == "get_beta":
        return calculate_beta(ctx.factory, input_["symbol"])
    if name == "calculate_wacc":
        return _calculate_wacc(**{k: v for k, v in input_.items() if v is not None})
    if name == "run_dcf":
        return _run_dcf(**input_)
    raise ToolCallError(f"unknown tool: {name}")


def _get_ratios(ctx: ValuationToolContext, symbol: str, periods: int) -> dict[str, Any]:
    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(FundamentalsQuarterly)
                .where(
                    FundamentalsQuarterly.symbol == symbol.upper(),
                    FundamentalsQuarterly.statement_type == StatementType.RATIOS.value,
                )
                .order_by(desc(FundamentalsQuarterly.fiscal_period_end))
                .limit(periods)
            )
            .scalars()
            .all()
        )
    return {
        "symbol": symbol.upper(),
        "count": len(rows),
        "periods": [
            {
                "fiscal_period_end": r.fiscal_period_end.isoformat(),
                "gross_margin": float(r.gross_margin) if r.gross_margin is not None else None,
                "operating_margin": float(r.operating_margin)
                if r.operating_margin is not None
                else None,
                "net_margin": float(r.net_margin) if r.net_margin is not None else None,
                "return_on_equity": float(r.return_on_equity)
                if r.return_on_equity is not None
                else None,
                "return_on_invested_capital": float(r.return_on_invested_capital)
                if r.return_on_invested_capital is not None
                else None,
                "debt_to_equity": float(r.debt_to_equity) if r.debt_to_equity is not None else None,
            }
            for r in rows
        ],
    }


def _get_latest_balance_sheet(ctx: ValuationToolContext, symbol: str) -> dict[str, Any]:
    with session_scope(ctx.factory) as s:
        row = s.execute(
            select(FundamentalsQuarterly)
            .where(
                FundamentalsQuarterly.symbol == symbol.upper(),
                FundamentalsQuarterly.statement_type == StatementType.BALANCE.value,
            )
            .order_by(desc(FundamentalsQuarterly.fiscal_period_end))
            .limit(1)
        ).scalar_one_or_none()
    if row is None:
        return {"error": "no balance sheet ingested for this symbol"}
    return {
        "symbol": symbol.upper(),
        "fiscal_period_end": row.fiscal_period_end.isoformat(),
        "total_debt": float(row.total_debt or 0),
        "cash_and_equivalents": float(row.cash_and_equivalents or 0),
        "net_debt": float((row.total_debt or 0) - (row.cash_and_equivalents or 0)),
        "shares_outstanding": float(row.shares_outstanding or 0),
        "total_equity": float(row.total_equity or 0),
    }
