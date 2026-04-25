"""Section 7: detector logic. Pure functions over seeded DB rows; no API process."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fii_db import EventType, NewsItem, PriceDaily, Ticker
from fii_db.session import session_scope
from fii_ingest.jobs.detectors import (
    detect_news_shocks,
    detect_price_shock,
    simulate_event_candidate,
)
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert


def _seed_flat_then_shock(factory, symbol: str, pct_today: float) -> None:
    """20 calm-but-nonzero bars (~0.5% daily moves) followed by a shock day."""
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )
        s.execute(delete(PriceDaily).where(PriceDaily.symbol == symbol))
        today = date.today()
        rows = []
        base = 100.0
        # Alternate +0.5% / -0.5% so the rolling std is non-zero but small (~0.5%).
        prev = base
        for i in range(21):
            d = today - timedelta(days=20 - i)
            close = prev * (1 + pct_today) if i == 20 else prev * (1.005 if i % 2 == 0 else 0.995)
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": d,
                    "open": Decimal(str(close)),
                    "high": Decimal(str(close)),
                    "low": Decimal(str(close)),
                    "close": Decimal(str(close)),
                    "adjusted_close": Decimal(str(close)),
                    "volume": 1_000_000,
                    "source": "test",
                }
            )
            prev = close
        s.execute(pg_insert(PriceDaily).values(rows))


def test_price_shock_fires_on_3_sigma(session_factory):
    symbol = "ZZSHOCK1"
    _seed_flat_then_shock(session_factory, symbol, pct_today=-0.08)
    with session_scope(session_factory) as s:
        cand = detect_price_shock(s, symbol=symbol)
    assert cand is not None
    assert cand.event_type == EventType.PRICE_SHOCK
    assert cand.payload["direction"] == "down"
    assert abs(cand.payload["pct_move"]) >= 5.0
    with session_scope(session_factory) as s:
        s.execute(delete(PriceDaily).where(PriceDaily.symbol == symbol))


def test_price_shock_quiet_returns_none(session_factory):
    symbol = "ZZSHOCK2"
    _seed_flat_then_shock(session_factory, symbol, pct_today=-0.005)  # 0.5% — not material
    with session_scope(session_factory) as s:
        cand = detect_price_shock(s, symbol=symbol)
    assert cand is None
    with session_scope(session_factory) as s:
        s.execute(delete(PriceDaily).where(PriceDaily.symbol == symbol))


def test_news_materiality_keyword_match(session_factory):
    symbol = "ZZNEWS"
    with session_scope(session_factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )
        s.execute(delete(NewsItem).where(NewsItem.content_hash == "test-hash-zznews"))
        s.execute(
            pg_insert(NewsItem).values(
                content_hash="test-hash-zznews",
                symbols=[symbol],
                headline=f"{symbol} cuts guidance after weak quarter",
                summary=None,
                url=None,
                source="test",
                published_at=__import__("datetime").datetime.utcnow(),
            )
        )

    with session_scope(session_factory) as s:
        cands = detect_news_shocks(s, since_hours=24)
    assert any(c.symbol == symbol for c in cands)
    with session_scope(session_factory) as s:
        s.execute(delete(NewsItem).where(NewsItem.content_hash == "test-hash-zznews"))


def test_simulate_event_candidate_supports_all_types():
    for et in EventType:
        c = simulate_event_candidate(symbol="AAPL", event_type=et)
        assert c.symbol == "AAPL"
        assert c.event_type == et
        assert c.payload.get("simulated") is True
