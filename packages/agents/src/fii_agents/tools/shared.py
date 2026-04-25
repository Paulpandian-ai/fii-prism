"""Shared deterministic helpers used by multiple specialist tool modules.

Pure-numpy implementations so we don't drag in pandas-ta. All math is auditable
and matches standard textbook formulas.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import structlog
from fii_db import MacroSeries, PriceDaily
from fii_db.session import session_scope
from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

log = structlog.get_logger(__name__)

# Default constants used across valuation / risk tools.
DEFAULT_EQUITY_RISK_PREMIUM = 0.055  # 5.5% — Damodaran-style baseline
DEFAULT_BENCHMARK_SYMBOL = "SPY"


# --- Price series fetch -------------------------------------------------------------------


def get_daily_close_series(
    factory: sessionmaker, symbol: str, *, days: int = 252 * 3
) -> tuple[list[date], np.ndarray]:
    """Most recent N trading days of adjusted closes. Returns (dates, prices) ordered ascending."""
    cutoff = date.today() - timedelta(days=int(days * 1.5))  # over-fetch for non-trading days
    with session_scope(factory) as s:
        rows = s.execute(
            select(PriceDaily.trade_date, PriceDaily.adjusted_close, PriceDaily.close)
            .where(PriceDaily.symbol == symbol.upper(), PriceDaily.trade_date >= cutoff)
            .order_by(PriceDaily.trade_date.asc())
        ).all()
    if not rows:
        return [], np.array([], dtype=float)
    dates = [r[0] for r in rows]
    prices = np.array(
        [float(r[1] if r[1] is not None else r[2] or 0.0) for r in rows],
        dtype=float,
    )
    return dates, prices


def daily_returns(prices: np.ndarray) -> np.ndarray:
    if prices.size < 2:
        return np.array([], dtype=float)
    return np.diff(prices) / prices[:-1]


# --- Beta, correlation, vol ---------------------------------------------------------------


def calculate_beta(
    factory: sessionmaker,
    symbol: str,
    *,
    benchmark: str = DEFAULT_BENCHMARK_SYMBOL,
    years: int = 3,
) -> dict[str, Any]:
    """3-year beta vs benchmark from daily returns. Returns NaN-safe dict.

    beta = cov(stock, market) / var(market)
    """
    days = 252 * years
    _, sp = get_daily_close_series(factory, symbol, days=days)
    _, bp = get_daily_close_series(factory, benchmark, days=days)
    if sp.size < 60 or bp.size < 60:
        return {
            "beta": None,
            "samples": int(min(sp.size, bp.size)),
            "note": "insufficient price history",
        }
    n = min(sp.size, bp.size)
    sr = daily_returns(sp[-n:])
    br = daily_returns(bp[-n:])
    if sr.size < 60:
        return {"beta": None, "samples": int(sr.size), "note": "insufficient returns"}
    var_m = float(np.var(br, ddof=1))
    if var_m <= 0:
        return {"beta": None, "samples": int(sr.size), "note": "benchmark variance is zero"}
    cov = float(np.cov(sr, br, ddof=1)[0, 1])
    beta = cov / var_m
    return {
        "beta": float(beta),
        "samples": int(sr.size),
        "benchmark": benchmark,
        "note": None,
    }


def calculate_correlation(
    factory: sessionmaker, symbol_a: str, symbol_b: str, *, years: int = 3
) -> dict[str, Any]:
    days = 252 * years
    _, a = get_daily_close_series(factory, symbol_a, days=days)
    _, b = get_daily_close_series(factory, symbol_b, days=days)
    n = min(a.size, b.size)
    if n < 60:
        return {"correlation": None, "samples": n, "note": "insufficient history"}
    ar = daily_returns(a[-n:])
    br = daily_returns(b[-n:])
    if ar.size < 60:
        return {"correlation": None, "samples": int(ar.size), "note": "insufficient returns"}
    corr = float(np.corrcoef(ar, br)[0, 1])
    return {"correlation": corr, "samples": int(ar.size), "note": None}


def realized_vol_annualized(prices: np.ndarray, *, days: int = 30) -> float | None:
    if prices.size < days + 1:
        return None
    rets = daily_returns(prices[-(days + 1) :])
    if rets.size < days // 2:
        return None
    return float(np.std(rets, ddof=1) * math.sqrt(252))


# --- Yield curve / risk-free rate ---------------------------------------------------------


def get_latest_macro_value(factory: sessionmaker, series_id: str) -> dict[str, Any]:
    with session_scope(factory) as s:
        row = s.execute(
            select(MacroSeries.observation_date, MacroSeries.value)
            .where(MacroSeries.series_id == series_id)
            .order_by(desc(MacroSeries.observation_date))
            .limit(1)
        ).first()
    if row is None or row[1] is None:
        return {
            "series_id": series_id,
            "value": None,
            "as_of": None,
            "note": "not yet ingested; run `fii-ingest macro`",
        }
    val = row[1]
    if isinstance(val, Decimal):
        val = float(val)
    return {"series_id": series_id, "value": float(val), "as_of": row[0].isoformat(), "note": None}


def get_yield_curve(factory: sessionmaker) -> dict[str, Any]:
    """Fetch the standard tenors we ingest. Pretty-prints the 2s10s spread."""
    series = ["DGS2", "DGS10"]
    out: dict[str, Any] = {}
    for s in series:
        out[s] = get_latest_macro_value(factory, s)
    two = out["DGS2"]["value"]
    ten = out["DGS10"]["value"]
    spread = ten - two if (two is not None and ten is not None) else None
    out["spread_10y_minus_2y"] = spread
    out["inverted"] = spread is not None and spread < 0
    return out


# --- Kelly criterion ---------------------------------------------------------------------


def kelly_fraction(*, expected_return: float, vol: float) -> float:
    """Kelly fraction in continuous form: f* = (mu - r) / sigma^2, with rf=0 and capped.

    Returns the Kelly-optimal fraction of capital (0..1) given expected return and volatility.
    For investing we typically use half-Kelly; that's the caller's call.
    """
    if vol <= 0:
        return 0.0
    f = expected_return / (vol * vol)
    return float(max(0.0, min(1.0, f)))


# --- Standard technical indicators (pure numpy) ------------------------------------------


def ema(values: np.ndarray, period: int) -> np.ndarray:
    if values.size == 0 or period <= 0:
        return np.array([], dtype=float)
    alpha = 2.0 / (period + 1)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, values.size):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def sma(values: np.ndarray, period: int) -> np.ndarray:
    if values.size < period:
        return np.array([], dtype=float)
    cumsum = np.cumsum(np.insert(values, 0, 0.0))
    return (cumsum[period:] - cumsum[:-period]) / period


def rsi(values: np.ndarray, period: int = 14) -> float | None:
    if values.size < period + 1:
        return None
    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, deltas.size):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def macd(values: np.ndarray, *, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, Any]:
    if values.size < slow + signal:
        return {"macd": None, "signal": None, "histogram": None, "cross": "no_signal"}
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    cross = "no_signal"
    if hist.size >= 2:
        if hist[-1] > 0 >= hist[-2]:
            cross = "bullish_cross"
        elif hist[-1] < 0 <= hist[-2]:
            cross = "bearish_cross"
    return {
        "macd": float(macd_line[-1]),
        "signal": float(signal_line[-1]),
        "histogram": float(hist[-1]),
        "cross": cross,
    }


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float | None:
    """Wilder's ADX. Inputs must be aligned arrays of equal length."""
    n = min(high.size, low.size, close.size)
    if n < period * 2:
        return None
    h, lo, c = high[-n:], low[-n:], close[-n:]
    tr = np.maximum.reduce([h[1:] - lo[1:], np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])])
    up = h[1:] - h[:-1]
    down = lo[:-1] - lo[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    def _wilder_smooth(x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        out[period - 1] = x[:period].sum()
        for i in range(period, x.size):
            out[i] = out[i - 1] - (out[i - 1] / period) + x[i]
        return out

    tr_s = _wilder_smooth(tr)
    plus_dm_s = _wilder_smooth(plus_dm)
    minus_dm_s = _wilder_smooth(minus_dm)
    plus_di = 100.0 * (plus_dm_s[period - 1 :] / tr_s[period - 1 :])
    minus_di = 100.0 * (minus_dm_s[period - 1 :] / tr_s[period - 1 :])
    dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)
    if dx.size < period:
        return None
    adx_v = np.mean(dx[-period:])
    return float(adx_v)


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float | None:
    n = min(high.size, low.size, close.size)
    if n < period + 1:
        return None
    h, lo, c = high[-n:], low[-n:], close[-n:]
    tr = np.maximum.reduce([h[1:] - lo[1:], np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])])
    atr_v = float(np.mean(tr[-period:]))
    return atr_v


def support_resistance(
    values: np.ndarray, *, lookback: int = 60, buckets: int = 8
) -> dict[str, list[float]]:
    """Approximate S/R via a histogram of closes — simple and good enough for an indicator
    table the LLM consumes (the LLM picks two of each)."""
    if values.size == 0:
        return {"support": [], "resistance": []}
    window = values[-lookback:]
    last = float(window[-1])
    _hist, edges = np.histogram(window, bins=buckets)
    centers = (edges[:-1] + edges[1:]) / 2.0
    supports = sorted(centers[centers < last].tolist(), reverse=True)[:2]
    resistances = sorted(centers[centers > last].tolist())[:2]
    return {
        "support": [round(x, 2) for x in supports],
        "resistance": [round(x, 2) for x in resistances],
    }


# --- DCF -----------------------------------------------------------------------------------


def run_dcf(
    *,
    base_revenue: float,
    revenue_growth: list[float],
    operating_margin: float,
    tax_rate: float,
    capex_pct_of_revenue: float,
    da_pct_of_revenue: float,
    nwc_pct_of_revenue: float,
    wacc: float,
    terminal_growth: float,
    shares_outstanding: float,
    net_debt: float,
) -> dict[str, Any]:
    """Two-stage DCF. Projects FCF for `len(revenue_growth)` years, then a Gordon growth
    terminal value. Returns intrinsic equity value per share + the projection table.

    The agent supplies assumptions; we do the math. No LLM arithmetic.
    """
    if not revenue_growth:
        return {"error": "revenue_growth must have at least one year"}
    if wacc <= terminal_growth:
        return {"error": f"wacc ({wacc}) must exceed terminal_growth ({terminal_growth})"}

    rows: list[dict[str, float]] = []
    rev = base_revenue
    pv_fcf = 0.0
    for i, g in enumerate(revenue_growth, start=1):
        rev = rev * (1.0 + g)
        ebit = rev * operating_margin
        nopat = ebit * (1.0 - tax_rate)
        capex = rev * capex_pct_of_revenue
        da = rev * da_pct_of_revenue
        nwc_change = (rev * nwc_pct_of_revenue) - (
            rows[-1]["revenue"] * nwc_pct_of_revenue if rows else base_revenue * nwc_pct_of_revenue
        )
        fcf = nopat + da - capex - nwc_change
        discount = (1.0 + wacc) ** i
        pv = fcf / discount
        pv_fcf += pv
        rows.append(
            {
                "year": i,
                "revenue": rev,
                "ebit": ebit,
                "nopat": nopat,
                "fcf": fcf,
                "pv": pv,
            }
        )

    # Terminal value at end of projection.
    terminal_fcf = rows[-1]["fcf"] * (1.0 + terminal_growth)
    terminal_value = terminal_fcf / (wacc - terminal_growth)
    pv_terminal = terminal_value / ((1.0 + wacc) ** len(revenue_growth))

    enterprise_value = pv_fcf + pv_terminal
    equity_value = enterprise_value - net_debt
    per_share = equity_value / shares_outstanding if shares_outstanding > 0 else None

    return {
        "intrinsic_value_per_share": per_share,
        "equity_value_usd": equity_value,
        "enterprise_value_usd": enterprise_value,
        "pv_of_fcf_usd": pv_fcf,
        "pv_of_terminal_usd": pv_terminal,
        "wacc": wacc,
        "terminal_growth": terminal_growth,
        "rows": rows,
    }


def calculate_wacc(
    *,
    risk_free_rate: float,
    beta: float,
    equity_risk_premium: float = DEFAULT_EQUITY_RISK_PREMIUM,
    cost_of_debt_after_tax: float | None = None,
    debt_to_equity: float = 0.0,
) -> dict[str, Any]:
    """CAPM cost of equity + optional weighted cost of debt."""
    cost_of_equity = risk_free_rate + beta * equity_risk_premium
    if cost_of_debt_after_tax is None or debt_to_equity <= 0:
        return {
            "wacc": cost_of_equity,
            "cost_of_equity": cost_of_equity,
            "weight_equity": 1.0,
            "weight_debt": 0.0,
        }
    e_weight = 1.0 / (1.0 + debt_to_equity)
    d_weight = debt_to_equity / (1.0 + debt_to_equity)
    wacc = e_weight * cost_of_equity + d_weight * cost_of_debt_after_tax
    return {
        "wacc": wacc,
        "cost_of_equity": cost_of_equity,
        "weight_equity": e_weight,
        "weight_debt": d_weight,
        "cost_of_debt_after_tax": cost_of_debt_after_tax,
    }
