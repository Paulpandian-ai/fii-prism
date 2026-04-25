"""Section 8: deterministic advisor tools.

Each test seeds the DB, calls the tool dispatch, and asserts the structured payload.
Run-quick-analysis is covered indirectly via the agent test below; here we verify the
deterministic ones (portfolio, watchlist, latest analysis, peer comp, tax-loss, impact).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fii_agents.advisor.tools import (
    AdvisorToolContext,
    dispatch,
    ensure_user_settings_row,
)
from fii_db import (
    Analysis,
    AnalysisRecommendation,
    AnalysisStatus,
    AnalysisType,
    Confidence,
    Position,
    PriceDaily,
    Ticker,
    UserSettings,
    Watchlist,
)
from fii_db.models import SINGLE_USER_ID
from fii_db.session import session_scope
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert


@pytest.fixture
def ctx(session_factory):
    ensure_user_settings_row(session_factory, SINGLE_USER_ID)
    return AdvisorToolContext(factory=session_factory, user_id=SINGLE_USER_ID)


def _seed_ticker(factory, symbol: str, sector: str | None = None) -> None:
    with session_scope(factory) as s:
        if sector is not None:
            s.execute(
                pg_insert(Ticker)
                .values(symbol=symbol, name=symbol, sector=sector)
                .on_conflict_do_update(index_elements=[Ticker.symbol], set_={"sector": sector})
            )
        else:
            s.execute(
                pg_insert(Ticker)
                .values(symbol=symbol, name=symbol)
                .on_conflict_do_nothing(index_elements=[Ticker.symbol])
            )


def _seed_close(factory, symbol: str, close: float) -> None:
    with session_scope(factory) as s:
        s.execute(delete(PriceDaily).where(PriceDaily.symbol == symbol))
        s.execute(
            pg_insert(PriceDaily).values(
                symbol=symbol,
                trade_date=date.today(),
                close=Decimal(str(close)),
                adjusted_close=Decimal(str(close)),
                volume=1_000_000,
                source="test",
            )
        )


def _seed_position(factory, symbol: str, shares: float, cost: float, opened_days_ago: int):
    pid = str(uuid.uuid4())
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Position).values(
                id=pid,
                user_id=SINGLE_USER_ID,
                symbol=symbol,
                shares=Decimal(str(shares)),
                cost_basis=Decimal(str(cost)),
                opened_at=datetime.now(UTC) - timedelta(days=opened_days_ago),
            )
        )
    return pid


@pytest.mark.asyncio
async def test_get_my_portfolio_returns_positions_with_marks(session_factory, ctx):
    with session_scope(session_factory) as s:
        s.execute(delete(Position).where(Position.user_id == SINGLE_USER_ID))
    _seed_ticker(session_factory, "ZZAAA", sector="Tech")
    _seed_close(session_factory, "ZZAAA", 150.0)
    _seed_position(session_factory, "ZZAAA", shares=10, cost=100, opened_days_ago=400)
    with session_scope(session_factory) as s:
        s.execute(
            pg_insert(UserSettings)
            .values(user_id=SINGLE_USER_ID, cash_balance_usd=Decimal("5000"))
            .on_conflict_do_update(
                index_elements=[UserSettings.user_id],
                set_={"cash_balance_usd": Decimal("5000")},
            )
        )

    result = await dispatch("get_my_portfolio", {}, ctx)
    assert result["cash_balance_usd"] == 5000.0
    assert any(p["symbol"] == "ZZAAA" for p in result["positions"])
    pos = next(p for p in result["positions"] if p["symbol"] == "ZZAAA")
    assert pos["current_price"] == 150.0
    assert pos["market_value_usd"] == 1500.0
    assert pos["unrealized_pl_usd"] == 500.0
    assert pos["long_term_eligible"] is True


@pytest.mark.asyncio
async def test_get_latest_analysis_age_days(session_factory, ctx):
    _seed_ticker(session_factory, "ZZBBB")
    aid = str(uuid.uuid4())
    with session_scope(session_factory) as s:
        s.execute(delete(Analysis).where(Analysis.symbol == "ZZBBB"))
        s.execute(
            pg_insert(Analysis).values(
                analysis_id=aid,
                symbol="ZZBBB",
                analysis_type=AnalysisType.DEEP_DIVE.value,
                status=AnalysisStatus.SUCCEEDED.value,
                initiated_at=datetime.now(UTC) - timedelta(days=10),
                completed_at=datetime.now(UTC) - timedelta(days=10),
                recommendation=AnalysisRecommendation.HOLD.value,
                confidence=Confidence.MEDIUM.value,
                fii_score=Decimal("6.4"),
            )
        )

    result = await dispatch("get_latest_analysis", {"symbol": "ZZBBB"}, ctx)
    assert result["status"] == "found"
    assert result["analysis_id"] == aid
    assert result["age_days"] >= 10
    assert result["is_stale"] is True
    assert result["recommendation"] == "hold"
    assert result["fii_score"] == 6.4


@pytest.mark.asyncio
async def test_get_latest_analysis_missing_returns_not_found(session_factory, ctx):
    result = await dispatch("get_latest_analysis", {"symbol": "ZNOPE"}, ctx)
    assert result["status"] == "not_found"
    assert "deep_dive" in result["note"]


@pytest.mark.asyncio
async def test_peer_comparison_uses_sector(session_factory, ctx):
    _seed_ticker(session_factory, "ZZTGT", sector="Energy")
    _seed_ticker(session_factory, "ZZP1", sector="Energy")
    _seed_ticker(session_factory, "ZZP2", sector="Energy")
    _seed_ticker(session_factory, "ZZOTHER", sector="Tech")

    result = await dispatch("get_peer_comparison", {"symbol": "ZZTGT", "limit": 5}, ctx)
    assert result["status"] == "ok"
    assert result["target_sector"] == "Energy"
    peer_syms = {p["symbol"] for p in result["peers"]}
    assert "ZZP1" in peer_syms or "ZZP2" in peer_syms
    assert "ZZOTHER" not in peer_syms


@pytest.mark.asyncio
async def test_tax_loss_harvest_short_term(session_factory, ctx):
    _seed_ticker(session_factory, "ZZTAX")
    _seed_close(session_factory, "ZZTAX", 80.0)
    with session_scope(session_factory) as s:
        s.execute(delete(Position).where(Position.symbol == "ZZTAX"))
    _seed_position(session_factory, "ZZTAX", shares=100, cost=120, opened_days_ago=180)

    result = await dispatch(
        "calculate_tax_loss_harvest",
        {"symbol": "ZZTAX", "shares": 100},
        ctx,
    )
    assert result["status"] == "ok"
    assert result["short_term_gain_usd"] == pytest.approx(-4000.0, rel=1e-6)
    # No tax owed on a loss.
    assert result["estimated_tax_usd"] == 0.0
    assert all(lot["term"] == "short" for lot in result["lots"])


@pytest.mark.asyncio
async def test_tax_loss_harvest_long_term_gain(session_factory, ctx):
    _seed_ticker(session_factory, "ZZGAIN")
    _seed_close(session_factory, "ZZGAIN", 200.0)
    with session_scope(session_factory) as s:
        s.execute(delete(Position).where(Position.symbol == "ZZGAIN"))
    _seed_position(session_factory, "ZZGAIN", shares=50, cost=100, opened_days_ago=400)

    result = await dispatch(
        "calculate_tax_loss_harvest",
        {"symbol": "ZZGAIN", "shares": 50},
        ctx,
    )
    assert result["status"] == "ok"
    assert result["long_term_gain_usd"] == pytest.approx(5000.0, rel=1e-6)
    # 5000 * 0.15 = 750
    assert result["estimated_tax_usd"] == pytest.approx(750.0, rel=1e-6)


@pytest.mark.asyncio
async def test_portfolio_impact_flags_concentration(session_factory, ctx):
    _seed_ticker(session_factory, "ZZBIG", sector="Tech")
    _seed_close(session_factory, "ZZBIG", 100.0)
    with session_scope(session_factory) as s:
        s.execute(delete(Position).where(Position.user_id == SINGLE_USER_ID))
        s.execute(
            pg_insert(UserSettings)
            .values(
                user_id=SINGLE_USER_ID,
                cash_balance_usd=Decimal("100000"),
                target_concentration_pct=Decimal("10"),
            )
            .on_conflict_do_update(
                index_elements=[UserSettings.user_id],
                set_={
                    "cash_balance_usd": Decimal("100000"),
                    "target_concentration_pct": Decimal("10"),
                },
            )
        )

    # Buy $50k of a $100k portfolio → 50% concentration, breach.
    result = await dispatch(
        "calculate_portfolio_impact",
        {"trades": [{"symbol": "ZZBIG", "side": "buy", "shares": 500}]},
        ctx,
    )
    assert result["status"] == "ok"
    assert any(b["symbol"] == "ZZBIG" for b in result["concentration_breaches"])
    assert result["cash_after_usd"] == 50000.0


@pytest.mark.asyncio
async def test_watchlist_tool(session_factory, ctx):
    _seed_ticker(session_factory, "ZZWA")
    with session_scope(session_factory) as s:
        s.execute(
            delete(Watchlist).where(Watchlist.user_id == SINGLE_USER_ID, Watchlist.symbol == "ZZWA")
        )
        s.execute(pg_insert(Watchlist).values(user_id=SINGLE_USER_ID, symbol="ZZWA"))

    result = await dispatch("get_my_watchlist", {}, ctx)
    assert any(w["symbol"] == "ZZWA" for w in result["watchlist"])
