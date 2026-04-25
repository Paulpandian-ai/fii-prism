"""Event detectors. Each detector returns a list of (symbol, EventType, payload)
candidates; the caller uses `fii_db.emit_event` to persist + NOTIFY (with built-in
15-minute debounce).

We keep detectors side-effect free except for the emit — they do not write back to
source tables — which makes them trivially testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fii_db import EventType, NewsItem, PriceDaily
from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)


# --- Data shape ---------------------------------------------------------------------------


@dataclass
class EventCandidate:
    symbol: str
    event_type: EventType
    payload: dict[str, Any]


# --- Price-shock detector -----------------------------------------------------------------

# 3-sigma vs the last 20 trading days' rolling std of log-returns. Sanity-cap the move
# at 5% absolute to avoid degenerate signals on new listings with too few bars.
PRICE_SHOCK_Z = 3.0
PRICE_SHOCK_MIN_BARS = 21  # need 20 prior closes + today


def detect_price_shock(
    session: Session, *, symbol: str, as_of: date | None = None
) -> EventCandidate | None:
    """Z-score of today's log-return vs the 20-bar rolling std. Returns a candidate
    when |z| >= 3 (and >= 5% absolute move), else None."""
    as_of = as_of or date.today()
    bars = session.execute(
        select(PriceDaily.trade_date, PriceDaily.close)
        .where(
            and_(
                PriceDaily.symbol == symbol.upper(),
                PriceDaily.trade_date <= as_of,
                PriceDaily.close.is_not(None),
            )
        )
        .order_by(desc(PriceDaily.trade_date))
        .limit(PRICE_SHOCK_MIN_BARS)
    ).all()
    if len(bars) < PRICE_SHOCK_MIN_BARS:
        return None

    # bars is newest-first; reverse to chronological for clarity.
    ordered = list(reversed(bars))
    closes = [float(c) for _, c in ordered if c is not None]
    if len(closes) < PRICE_SHOCK_MIN_BARS or any(c <= 0 for c in closes):
        return None

    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    today_ret = log_returns[-1]
    prior = log_returns[:-1]
    if len(prior) < 2:
        return None
    mean = sum(prior) / len(prior)
    var = sum((r - mean) ** 2 for r in prior) / (len(prior) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    z = (today_ret - mean) / sd
    pct_move = (closes[-1] / closes[-2]) - 1

    if abs(z) < PRICE_SHOCK_Z or abs(pct_move) < 0.05:
        return None

    return EventCandidate(
        symbol=symbol.upper(),
        event_type=EventType.PRICE_SHOCK,
        payload={
            "z": round(z, 3),
            "pct_move": round(pct_move * 100, 3),
            "direction": "up" if today_ret > 0 else "down",
            "close": float(closes[-1]),
            "prev_close": float(closes[-2]),
            "as_of": as_of.isoformat(),
            "rolling_sd_20d": round(sd, 5),
        },
    )


# --- News materiality heuristic -----------------------------------------------------------

# Conservative keyword bag. A proper Haiku-classifier plugs in here later; for now we
# flag the unambiguous ones and rely on the 15-min debounce to throttle near-duplicates.
_MATERIAL_KEYWORDS = {
    "earnings beat",
    "earnings miss",
    "guidance cut",
    "guidance raised",
    "cuts guidance",
    "raises guidance",
    "downgrade",
    "upgrade",
    "lawsuit",
    "settles",
    "investigation",
    "resigns",
    "ceo steps down",
    "acquires",
    "to acquire",
    "merger",
    "takeover",
    "fda approval",
    "fda rejects",
    "recall",
    "bankruptcy",
    "chapter 11",
    "files for bankruptcy",
}


def _is_material_headline(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in _MATERIAL_KEYWORDS)


def detect_news_shocks(
    session: Session,
    *,
    since_hours: int = 6,
    limit: int = 100,
) -> list[EventCandidate]:
    """Scan recent news for materiality-keyword hits. One candidate per (symbol, news_id).

    Production will swap this keyword match with a Haiku 4.5 classifier prompt gated by
    ANTHROPIC_API_KEY. The keyword fallback keeps the pipeline green without a key.
    """
    from datetime import datetime

    cutoff = datetime.utcnow() - timedelta(hours=since_hours)
    rows = session.execute(
        select(NewsItem.news_id, NewsItem.symbols, NewsItem.headline, NewsItem.summary)
        .where(NewsItem.published_at.is_not(None))
        .where(NewsItem.published_at >= cutoff)
        .order_by(desc(NewsItem.published_at))
        .limit(limit)
    ).all()
    candidates: list[EventCandidate] = []
    for news_id, symbols, headline, summary in rows:
        if not symbols:
            continue
        text = f"{headline or ''}\n{summary or ''}"
        if not _is_material_headline(text):
            continue
        for sym in symbols:
            candidates.append(
                EventCandidate(
                    symbol=sym,
                    event_type=EventType.NEWS_SHOCK,
                    payload={
                        "news_id": str(news_id),
                        "headline": headline,
                        "matched_keywords": [kw for kw in _MATERIAL_KEYWORDS if kw in text.lower()][
                            :3
                        ],
                    },
                )
            )
    return candidates


# --- Simulation helper --------------------------------------------------------------------


def simulate_event_candidate(
    *,
    symbol: str,
    event_type: EventType,
    payload: dict[str, Any] | None = None,
) -> EventCandidate:
    """Build a synthetic candidate for demos / integration tests."""
    default_payload: dict[str, Any] = {
        EventType.PRICE_SHOCK: {
            "z": -4.2,
            "pct_move": -8.5,
            "direction": "down",
            "simulated": True,
        },
        EventType.NEWS_SHOCK: {
            "headline": "Simulated earnings miss",
            "matched_keywords": ["earnings miss"],
            "simulated": True,
        },
        EventType.EIGHT_K_FILED: {
            "accession_no": "0000000000-00-000000",
            "item": "2.02",
            "simulated": True,
        },
        EventType.EARNINGS_RELEASE: {
            "fiscal_period": "Q1",
            "simulated": True,
        },
        EventType.MACRO_SURPRISE: {
            "series_id": "CPIAUCSL",
            "surprise_pct": 1.5,
            "simulated": True,
        },
    }.get(event_type, {"simulated": True})
    merged = {**default_payload, **(payload or {})}
    return EventCandidate(
        symbol=symbol.upper(),
        event_type=event_type,
        payload=merged,
    )


# --- Decimal helper -----------------------------------------------------------------------


def _to_float(x: Decimal | float | None) -> float | None:
    if x is None:
        return None
    return float(x)
