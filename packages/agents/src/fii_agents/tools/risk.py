"""Tools for the Risk specialist (both preliminary and final passes).

Preliminary pass: purely quantitative — DFAST scenarios, ADV, correlation, Kelly.
Final pass (after debate): uses LLM to synthesize, but has access to the same
deterministic tools to verify any number it wants to cite.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import structlog
from fii_db import PriceDaily
from fii_db.session import session_scope
from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from fii_agents.tools.shared import (
    calculate_correlation,
    get_daily_close_series,
    kelly_fraction,
    realized_vol_annualized,
)

log = structlog.get_logger(__name__)

# Reused from FII v1.
DFAST_SCENARIOS: dict[str, float] = {
    "pullback": -0.15,
    "recession": -0.30,
    "severe": -0.50,
    "sector_shock": -0.20,
    "bull_rally": 0.25,
}


@dataclass
class RiskToolContext:
    factory: sessionmaker
    call_count: int = 0
    max_calls: int = 10


TOOLS: list[dict[str, Any]] = [
    {
        "name": "calculate_dfast_scenarios",
        "description": (
            "Five fixed scenarios in dollar terms (pullback/recession/severe/sector_shock/bull_rally). "
            "Reused unchanged from FII v1."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "position_value_usd": {"type": "number", "minimum": 0, "default": 10000.0},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_avg_daily_volume",
        "description": "Trailing 60-day average daily dollar volume — liquidity check.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_correlation_to_benchmark",
        "description": "3-year daily-returns correlation vs SPY (proxy for portfolio in MVP).",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "benchmark": {"type": "string", "default": "SPY"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "calculate_kelly_fraction",
        "description": (
            "Kelly-optimal capital fraction given expected return and annualized volatility. "
            "Returns the value capped to [0,1]; we typically apply half-Kelly in practice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expected_return": {"type": "number"},
                "vol": {"type": "number", "minimum": 0},
            },
            "required": ["expected_return", "vol"],
        },
    },
    {
        "name": "get_max_drawdown_historical",
        "description": "Max peak-to-trough drawdown over the available price history (as a ratio).",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]


class ToolCallError(Exception):
    pass


async def dispatch(name: str, input_: dict[str, Any], ctx: RiskToolContext) -> dict[str, Any]:
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
            specialist="risk",
            symbol=input_.get("symbol"),
            call_number=ctx.call_count,
            duration_ms=int((time.perf_counter() - start) * 1000),
            success=success,
        )


async def _route(name: str, input_: dict[str, Any], ctx: RiskToolContext) -> dict[str, Any]:
    if name == "calculate_dfast_scenarios":
        return _dfast(input_["symbol"], float(input_.get("position_value_usd") or 10000.0))
    if name == "get_avg_daily_volume":
        return _adv(ctx, input_["symbol"])
    if name == "get_correlation_to_benchmark":
        return calculate_correlation(
            ctx.factory, input_["symbol"], input_.get("benchmark") or "SPY"
        )
    if name == "calculate_kelly_fraction":
        return {
            "kelly_fraction": kelly_fraction(
                expected_return=float(input_["expected_return"]),
                vol=float(input_["vol"]),
            ),
            "note": "We typically apply half-Kelly in production (multiply by 0.5).",
        }
    if name == "get_max_drawdown_historical":
        return _max_drawdown(ctx, input_["symbol"])
    raise ToolCallError(f"unknown tool: {name}")


def _dfast(symbol: str, position_value_usd: float) -> dict[str, Any]:
    out: dict[str, Any] = {"symbol": symbol.upper(), "scenarios": {}}
    for name, move in DFAST_SCENARIOS.items():
        after = position_value_usd * (1.0 + move)
        out["scenarios"][name] = {
            "name": name,
            "assumed_move_pct": move,
            "position_value_start_usd": position_value_usd,
            "position_value_after_usd": after,
            "drawdown_usd": after - position_value_usd,
            "notes": None,
        }
    return out


def _adv(ctx: RiskToolContext, symbol: str) -> dict[str, Any]:
    cutoff = date.today() - timedelta(days=120)
    with session_scope(ctx.factory) as s:
        rows = s.execute(
            select(PriceDaily.close, PriceDaily.volume)
            .where(PriceDaily.symbol == symbol.upper(), PriceDaily.trade_date >= cutoff)
            .order_by(desc(PriceDaily.trade_date))
            .limit(60)
        ).all()
    if not rows:
        return {
            "symbol": symbol.upper(),
            "avg_daily_dollar_volume": None,
            "note": "no price history; run `fii-ingest prices-daily`",
        }
    dollars = [float(r[0] or 0) * float(r[1] or 0) for r in rows]
    if not dollars:
        return {"symbol": symbol.upper(), "avg_daily_dollar_volume": None}
    return {
        "symbol": symbol.upper(),
        "avg_daily_dollar_volume": float(np.mean(dollars)),
        "samples": len(dollars),
    }


def _max_drawdown(ctx: RiskToolContext, symbol: str) -> dict[str, Any]:
    _, prices = get_daily_close_series(ctx.factory, symbol, days=252 * 10)
    if prices.size < 30:
        return {
            "symbol": symbol.upper(),
            "max_drawdown": None,
            "note": "insufficient price history",
        }
    peak = np.maximum.accumulate(prices)
    drawdown = (prices - peak) / peak
    mdd = float(drawdown.min())
    vol_30d = realized_vol_annualized(prices, days=30)
    return {
        "symbol": symbol.upper(),
        "max_drawdown": mdd,
        "samples": int(prices.size),
        "realized_vol_30d_annualized": vol_30d,
    }
