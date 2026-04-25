"""Tools for the Technical specialist.

The agent INTERPRETS pre-computed indicators. Its system prompt forbids it from
attempting to compute or estimate anything itself — the only way to obtain a
number is to call get_indicators().
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
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from fii_agents.tools.shared import (
    adx as _adx,
)
from fii_agents.tools.shared import (
    atr as _atr,
)
from fii_agents.tools.shared import (
    get_daily_close_series,
    realized_vol_annualized,
    sma,
    support_resistance,
)
from fii_agents.tools.shared import (
    macd as _macd,
)
from fii_agents.tools.shared import (
    rsi as _rsi,
)

log = structlog.get_logger(__name__)


@dataclass
class TechnicalToolContext:
    factory: sessionmaker
    call_count: int = 0
    max_calls: int = 4  # Tight — this specialist mostly just reads one big dict.


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_indicators",
        "description": (
            "Returns the full pre-computed indicator dict for a symbol. RSI(14), MACD(12,26,9), "
            "ADX(14), ATR(14), realized_vol_30d, SMA(20/50/200), Bollinger(20,2), "
            "support/resistance, and the most recent close. Do NOT compute any indicator "
            "yourself; if a value is null, report it as missing in your output."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "lookback_days": {
                    "type": "integer",
                    "minimum": 60,
                    "maximum": 1500,
                    "default": 365,
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_recent_closes",
        "description": "Last N daily closes (date + price) for chart context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "days": {"type": "integer", "minimum": 5, "maximum": 252, "default": 60},
            },
            "required": ["symbol"],
        },
    },
]


class ToolCallError(Exception):
    pass


async def dispatch(name: str, input_: dict[str, Any], ctx: TechnicalToolContext) -> dict[str, Any]:
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
            specialist="technical",
            symbol=input_.get("symbol"),
            call_number=ctx.call_count,
            duration_ms=int((time.perf_counter() - start) * 1000),
            success=success,
        )


async def _route(name: str, input_: dict[str, Any], ctx: TechnicalToolContext) -> dict[str, Any]:
    if name == "get_indicators":
        return _get_indicators(ctx, input_["symbol"], int(input_.get("lookback_days") or 365))
    if name == "get_recent_closes":
        return _get_recent_closes(ctx, input_["symbol"], int(input_.get("days") or 60))
    raise ToolCallError(f"unknown tool: {name}")


def _get_indicators(ctx: TechnicalToolContext, symbol: str, lookback_days: int) -> dict[str, Any]:
    cutoff = date.today() - timedelta(days=int(lookback_days * 1.5))
    with session_scope(ctx.factory) as s:
        rows = s.execute(
            select(
                PriceDaily.trade_date,
                PriceDaily.high,
                PriceDaily.low,
                PriceDaily.close,
                PriceDaily.adjusted_close,
            )
            .where(PriceDaily.symbol == symbol.upper(), PriceDaily.trade_date >= cutoff)
            .order_by(PriceDaily.trade_date.asc())
        ).all()
    if not rows:
        return {
            "error": f"no price history for {symbol}; run `fii-ingest prices-daily --ticker {symbol}`"
        }
    closes = np.array([float(r[4] if r[4] is not None else r[3] or 0.0) for r in rows], dtype=float)
    highs = np.array([float(r[1] or r[3] or 0.0) for r in rows], dtype=float)
    lows = np.array([float(r[2] or r[3] or 0.0) for r in rows], dtype=float)

    sr = support_resistance(closes)
    macd_v = _macd(closes)
    sma_20 = sma(closes, 20)
    sma_50 = sma(closes, 50)
    sma_200 = sma(closes, 200)
    last = float(closes[-1])

    def _trend(s: np.ndarray) -> str:
        if s.size == 0:
            return "sideways"
        if last > s[-1] * 1.02:
            return "up"
        if last < s[-1] * 0.98:
            return "down"
        return "sideways"

    return {
        "symbol": symbol.upper(),
        "as_of": rows[-1][0].isoformat(),
        "last_close": last,
        "rsi_14": _rsi(closes, 14),
        "macd": macd_v,
        "adx_14": _adx(highs, lows, closes, 14),
        "atr_14": _atr(highs, lows, closes, 14),
        "realized_vol_30d": realized_vol_annualized(closes, days=30),
        "sma_20": float(sma_20[-1]) if sma_20.size else None,
        "sma_50": float(sma_50[-1]) if sma_50.size else None,
        "sma_200": float(sma_200[-1]) if sma_200.size else None,
        "trend_short": _trend(sma_20),
        "trend_medium": _trend(sma_50),
        "trend_long": _trend(sma_200),
        "support_levels": sr["support"],
        "resistance_levels": sr["resistance"],
        "samples": int(closes.size),
    }


def _get_recent_closes(ctx: TechnicalToolContext, symbol: str, days: int) -> dict[str, Any]:
    dts, prices = get_daily_close_series(ctx.factory, symbol, days=days)
    points = [{"date": d.isoformat(), "close": float(p)} for d, p in zip(dts, prices, strict=True)]
    return {"symbol": symbol.upper(), "count": len(points), "closes": points[-days:]}
