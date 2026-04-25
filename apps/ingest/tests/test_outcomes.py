"""Section 9: outcomes computation — returns + alpha + hit/miss + dominance."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fii_db import (
    Analysis,
    AnalysisOutcome,
    AnalysisRecommendation,
    AnalysisSpecialistOutput,
    AnalysisStatus,
    AnalysisType,
    Confidence,
    PriceDaily,
    SpecialistName,
    Ticker,
)
from fii_db.session import session_scope
from fii_ingest.jobs.outcomes import (
    HORIZONS_DAYS,
    _classify_hit,
    compute_outcomes,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


def _seed_ticker(factory, symbol: str) -> None:
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )


def _seed_close(factory, symbol: str, d: date, close: float) -> None:
    with session_scope(factory) as s:
        s.execute(
            pg_insert(PriceDaily)
            .values(
                symbol=symbol,
                trade_date=d,
                close=Decimal(str(close)),
                adjusted_close=Decimal(str(close)),
                volume=1_000_000,
                source="test",
            )
            .on_conflict_do_update(
                index_elements=[PriceDaily.symbol, PriceDaily.trade_date],
                set_={"close": Decimal(str(close)), "adjusted_close": Decimal(str(close))},
            )
        )


def _seed_journaled(
    factory,
    *,
    symbol: str,
    recommendation: str,
    confidence: str,
    bull_confidence: str = "high",
    bear_confidence: str = "low",
    days_ago: int = 35,
) -> str:
    aid = str(uuid.uuid4())
    started = datetime.now(UTC) - timedelta(days=days_ago)
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Analysis).values(
                analysis_id=aid,
                symbol=symbol,
                analysis_type=AnalysisType.DEEP_DIVE.value,
                status=AnalysisStatus.SUCCEEDED.value,
                initiated_at=started,
                completed_at=started,
                recommendation=recommendation,
                confidence=confidence,
                fii_score=Decimal("7.0"),
                action_taken="bought",
                action_size_usd=Decimal("1000"),
                action_price=Decimal("100"),
                action_at=started,
            )
        )
        for spec, conf in (
            (SpecialistName.BULL.value, bull_confidence),
            (SpecialistName.BEAR.value, bear_confidence),
        ):
            s.execute(
                pg_insert(AnalysisSpecialistOutput)
                .values(
                    analysis_id=aid,
                    specialist_name=spec,
                    output_json={"confidence": conf, "case": "test"},
                )
                .on_conflict_do_nothing(constraint="uq_analysis_specialist")
            )
    return aid


def test_classify_hit_buy_uses_alpha():
    assert _classify_hit("buy", 0.05, 0.02) is True
    assert _classify_hit("buy", -0.05, -0.02) is False


def test_classify_hit_buy_falls_back_to_return_when_no_alpha():
    assert _classify_hit("buy", 0.05, None) is True
    assert _classify_hit("buy", -0.05, None) is False


def test_classify_hit_sell_inverts():
    assert _classify_hit("sell", -0.05, -0.02) is True
    assert _classify_hit("sell", 0.05, 0.02) is False


def test_classify_hit_hold_uses_band():
    assert _classify_hit("hold", 0.03, None) is True
    assert _classify_hit("hold", 0.10, None) is False


def test_classify_hit_returns_none_for_missing_recommendation():
    assert _classify_hit(None, 0.05, 0.02) is None


def test_compute_outcomes_buy_with_spy_alpha(session_factory):
    sym = "ZZOUT1"
    _seed_ticker(session_factory, sym)
    _seed_ticker(session_factory, "SPY")
    today = date.today()
    entry_d = today - timedelta(days=35)
    _seed_close(session_factory, sym, entry_d, 100.0)
    _seed_close(session_factory, sym, entry_d + timedelta(days=30), 110.0)
    _seed_close(session_factory, "SPY", entry_d, 400.0)
    _seed_close(session_factory, "SPY", entry_d + timedelta(days=30), 408.0)  # +2%

    aid = _seed_journaled(
        session_factory,
        symbol=sym,
        recommendation=AnalysisRecommendation.BUY.value,
        confidence=Confidence.HIGH.value,
        days_ago=35,
    )

    report = compute_outcomes(session_factory, today=today)
    assert report.computed >= 1

    with session_scope(session_factory) as s:
        out = s.execute(
            select(AnalysisOutcome).where(AnalysisOutcome.analysis_id == aid)
        ).scalar_one()
    assert out.spy_available is True
    # +10% return, +2% SPY → +8% alpha
    assert out.return_1m is not None and abs(float(out.return_1m) - 0.10) < 1e-4
    assert out.alpha_1m is not None and abs(float(out.alpha_1m) - 0.08) < 1e-4
    assert out.hit_1m is True
    assert out.dominance == "bull"

    # Cleanup so re-runs are deterministic.
    with session_scope(session_factory) as s:
        s.execute(delete(AnalysisOutcome).where(AnalysisOutcome.analysis_id == aid))
        s.execute(delete(Analysis).where(Analysis.analysis_id == aid))
        s.execute(delete(PriceDaily).where(PriceDaily.symbol == sym))


def test_compute_outcomes_no_spy_fills_returns_only(session_factory):
    sym = "ZZOUT2"
    _seed_ticker(session_factory, sym)
    today = date.today()
    entry_d = today - timedelta(days=35)
    _seed_close(session_factory, sym, entry_d, 100.0)
    _seed_close(session_factory, sym, entry_d + timedelta(days=30), 95.0)
    # Wipe SPY so the test forces the no-SPY path.
    with session_scope(session_factory) as s:
        s.execute(delete(PriceDaily).where(PriceDaily.symbol == "SPY"))

    aid = _seed_journaled(
        session_factory,
        symbol=sym,
        recommendation=AnalysisRecommendation.SELL.value,
        confidence=Confidence.HIGH.value,
        bull_confidence="low",
        bear_confidence="high",
        days_ago=35,
    )

    compute_outcomes(session_factory, today=today)
    with session_scope(session_factory) as s:
        out = s.execute(
            select(AnalysisOutcome).where(AnalysisOutcome.analysis_id == aid)
        ).scalar_one()
    assert out.spy_available is False
    assert out.return_1m is not None and abs(float(out.return_1m) + 0.05) < 1e-4
    assert out.alpha_1m is None  # SPY not available
    # Sell + negative return → hit
    assert out.hit_1m is True
    assert out.dominance == "bear"

    with session_scope(session_factory) as s:
        s.execute(delete(AnalysisOutcome).where(AnalysisOutcome.analysis_id == aid))
        s.execute(delete(Analysis).where(Analysis.analysis_id == aid))
        s.execute(delete(PriceDaily).where(PriceDaily.symbol == sym))


def test_horizons_dict_has_six_entries():
    assert set(HORIZONS_DAYS) == {"1d", "1w", "1m", "3m", "6m", "1y"}
