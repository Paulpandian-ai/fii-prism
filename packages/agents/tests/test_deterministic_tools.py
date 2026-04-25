"""Unit tests for the deterministic tool helpers.

These don't require Postgres or Anthropic. They cover the pure-math helpers
in tools/shared.py that the LLM specialists rely on for every numeric claim.
"""

from __future__ import annotations

import numpy as np
import pytest
from fii_agents.tools.shared import (
    adx,
    atr,
    calculate_wacc,
    daily_returns,
    ema,
    kelly_fraction,
    realized_vol_annualized,
    rsi,
    run_dcf,
    sma,
    support_resistance,
)


def test_daily_returns_basic():
    p = np.array([100.0, 110.0, 121.0])
    r = daily_returns(p)
    assert r.size == 2
    assert pytest.approx(r[0]) == 0.10
    assert pytest.approx(r[1]) == 0.10


def test_ema_first_value_seeded():
    v = np.array([100.0, 110.0, 105.0, 115.0])
    e = ema(v, period=3)
    assert e[0] == 100.0
    assert e.size == v.size


def test_sma_window():
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    s = sma(v, period=3)
    assert s.tolist() == [2.0, 3.0, 4.0]


def test_rsi_all_gains_returns_100():
    v = np.array(list(range(1, 30)), dtype=float)  # strictly rising
    r = rsi(v, period=14)
    assert r == 100.0


def test_rsi_returns_none_when_insufficient_data():
    assert rsi(np.array([1.0, 2.0, 3.0]), period=14) is None


def test_kelly_fraction_bounded():
    # Math: 0.10/0.04 = 2.5, but the function caps at 1.0 (no leverage).
    assert kelly_fraction(expected_return=0.10, vol=0.20) == 1.0
    # Negative expected return -> never short.
    assert kelly_fraction(expected_return=-0.05, vol=0.20) == 0.0
    # In-bounds: 0.02/0.04 = 0.5
    assert kelly_fraction(expected_return=0.02, vol=0.20) == pytest.approx(0.5)


def test_calculate_wacc_pure_equity():
    wacc = calculate_wacc(risk_free_rate=0.04, beta=1.2)
    assert wacc["wacc"] == pytest.approx(0.04 + 1.2 * 0.055)
    assert wacc["weight_equity"] == 1.0


def test_calculate_wacc_with_debt():
    wacc = calculate_wacc(
        risk_free_rate=0.04,
        beta=1.0,
        equity_risk_premium=0.05,
        cost_of_debt_after_tax=0.04,
        debt_to_equity=1.0,
    )
    # Cost of equity = 0.04 + 1.0*0.05 = 0.09; weights 0.5/0.5
    assert wacc["wacc"] == pytest.approx(0.5 * 0.09 + 0.5 * 0.04)


def test_run_dcf_per_share_positive_for_reasonable_assumptions():
    out = run_dcf(
        base_revenue=100_000.0,
        revenue_growth=[0.05] * 5,
        operating_margin=0.20,
        tax_rate=0.21,
        capex_pct_of_revenue=0.05,
        da_pct_of_revenue=0.05,
        nwc_pct_of_revenue=0.0,
        wacc=0.10,
        terminal_growth=0.025,
        shares_outstanding=1_000.0,
        net_debt=0.0,
    )
    assert "intrinsic_value_per_share" in out
    assert out["intrinsic_value_per_share"] is not None
    assert out["intrinsic_value_per_share"] > 0
    assert len(out["rows"]) == 5


def test_run_dcf_rejects_terminal_above_wacc():
    out = run_dcf(
        base_revenue=100.0,
        revenue_growth=[0.02],
        operating_margin=0.1,
        tax_rate=0.21,
        capex_pct_of_revenue=0.05,
        da_pct_of_revenue=0.05,
        nwc_pct_of_revenue=0.0,
        wacc=0.05,
        terminal_growth=0.05,  # equal — invalid
        shares_outstanding=1.0,
        net_debt=0.0,
    )
    assert "error" in out


def test_atr_and_adx_handle_short_series():
    h = np.array([1.0, 2.0])
    lo = np.array([0.5, 1.5])
    c = np.array([0.8, 1.8])
    assert atr(h, lo, c, 14) is None
    assert adx(h, lo, c, 14) is None


def test_realized_vol_annualizes_with_sqrt_252():
    # Constant 1% daily moves -> realized vol = 0.01 * sqrt(252)
    rng = np.random.default_rng(42)
    rets = rng.normal(0, 0.01, 253)  # need days+1 prices
    prices = 100 * np.exp(np.cumsum(rets))
    vol = realized_vol_annualized(prices, days=252)
    assert vol is not None
    # 0.01 daily * sqrt(252) ≈ 0.159; loose bounds for a single 252-sample seed.
    assert 0.05 < vol < 0.30


def test_support_resistance_picks_levels_below_and_above():
    rng = np.random.default_rng(0)
    prices = 100 + np.cumsum(rng.normal(0, 1, 200))
    sr = support_resistance(prices)
    last = float(prices[-1])
    assert all(s < last for s in sr["support"])
    assert all(r > last for r in sr["resistance"])
