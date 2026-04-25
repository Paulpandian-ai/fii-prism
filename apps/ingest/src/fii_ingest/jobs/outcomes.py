"""Compute analysis outcomes vs SPY at fixed horizons.

For every Analysis where action_taken != 'none', look up the close on action_at and
compute return + alpha at 1d / 1w / 1m / 3m / 6m / 1y. Idempotent — recomputes the
full row each run; horizons whose end-date is in the future stay null.

`dominance` is a heuristic: bull-dominant if recommendation in (buy, strong_buy) AND
bull confidence ≥ medium; bear-dominant if (trim, sell) AND bear confidence ≥ medium;
otherwise balanced.

`hit_<horizon>` classification:
- buy / strong_buy → True iff alpha > 0
- sell / trim     → True iff alpha < 0
- hold            → True iff |return| ≤ 5%
- anything else / no recommendation → null
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fii_db import Analysis, AnalysisOutcome, AnalysisSpecialistOutput, PriceDaily
from fii_db.session import session_scope
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

log = structlog.get_logger(__name__)


HORIZONS_DAYS: dict[str, int] = {
    "1d": 1,
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
}

HOLD_BAND = 0.05  # ±5%
SPY = "SPY"


@dataclass
class OutcomeReport:
    computed: int
    skipped_no_price: int
    skipped_pending: int


# --- Public API ---------------------------------------------------------------------------


def compute_outcomes(factory: sessionmaker, *, today: date | None = None) -> OutcomeReport:
    """Recompute outcomes for every journaled analysis. Idempotent."""
    today = today or date.today()
    computed = skipped_no_price = skipped_pending = 0

    with session_scope(factory) as s:
        rows = s.execute(select(Analysis).where(Analysis.action_taken != "none")).scalars().all()

    for a in rows:
        try:
            with session_scope(factory) as s:
                report = _compute_one(s, a, today=today)
            if report == "no_price":
                skipped_no_price += 1
            elif report == "pending":
                skipped_pending += 1
            else:
                computed += 1
        except Exception:
            log.exception("compute_outcome_failed", analysis_id=str(a.analysis_id))

    log.info(
        "outcomes_computed",
        computed=computed,
        skipped_no_price=skipped_no_price,
        skipped_pending=skipped_pending,
    )
    return OutcomeReport(
        computed=computed,
        skipped_no_price=skipped_no_price,
        skipped_pending=skipped_pending,
    )


# --- Per-analysis computation -------------------------------------------------------------


def _compute_one(s: Session, a: Analysis, *, today: date) -> str:
    if a.action_at is None:
        return "no_price"
    entry_date = a.action_at.date()

    entry_price = _close_on_or_after(s, a.symbol, entry_date)
    spy_entry = _close_on_or_after(s, SPY, entry_date)
    if entry_price is None:
        return "no_price"

    spy_available = spy_entry is not None
    horizons_data: dict[str, dict[str, Any]] = {}
    pending = True
    for horizon, days in HORIZONS_DAYS.items():
        target_date = entry_date + timedelta(days=days)
        if target_date > today:
            horizons_data[horizon] = {"return": None, "alpha": None, "hit": None}
            continue
        pending = False
        sym_close = _close_on_or_after(s, a.symbol, target_date)
        spy_close = _close_on_or_after(s, SPY, target_date) if spy_available else None
        if sym_close is None:
            horizons_data[horizon] = {"return": None, "alpha": None, "hit": None}
            continue
        ret = float(sym_close) / float(entry_price) - 1.0
        if spy_close is None or spy_entry is None:
            alpha = None
        else:
            spy_ret = float(spy_close) / float(spy_entry) - 1.0
            alpha = ret - spy_ret
        hit = _classify_hit(a.recommendation, ret, alpha)
        horizons_data[horizon] = {"return": ret, "alpha": alpha, "hit": hit}

    dominance = _dominance(s, a)

    values: dict[str, Any] = {
        "analysis_id": str(a.analysis_id),
        "symbol": a.symbol,
        "entry_price": entry_price,
        "entry_date": entry_date,
        "dominance": dominance,
        "spy_available": spy_available,
        "computed_at": datetime.utcnow(),
        "notes": None if spy_available else "SPY price history missing — alpha unavailable.",
    }
    for horizon, data in horizons_data.items():
        values[f"return_{horizon}"] = (
            Decimal(f"{data['return']:.5f}") if data["return"] is not None else None
        )
        values[f"alpha_{horizon}"] = (
            Decimal(f"{data['alpha']:.5f}") if data["alpha"] is not None else None
        )
        values[f"hit_{horizon}"] = data["hit"]

    stmt = pg_insert(AnalysisOutcome).values(**values)
    update_cols = {k: stmt.excluded[k] for k in values if k not in ("analysis_id",)}
    stmt = stmt.on_conflict_do_update(
        index_elements=[AnalysisOutcome.analysis_id], set_=update_cols
    )
    s.execute(stmt)
    return "pending" if pending else "ok"


def _close_on_or_after(s: Session, symbol: str, target_date: date) -> Decimal | None:
    """Get the close on target_date, or the next available trading day. Bounded scan
    of 7 days so a long weekend / holiday doesn't blow this up."""
    cutoff = target_date + timedelta(days=7)
    row = s.execute(
        select(PriceDaily.close)
        .where(
            PriceDaily.symbol == symbol.upper(),
            PriceDaily.trade_date >= target_date,
            PriceDaily.trade_date <= cutoff,
        )
        .order_by(PriceDaily.trade_date.asc())
        .limit(1)
    ).scalar_one_or_none()
    return row


def _classify_hit(recommendation: str | None, ret: float, alpha: float | None) -> bool | None:
    """See module docstring."""
    if recommendation is None:
        return None
    if recommendation in ("buy", "strong_buy"):
        if alpha is None:
            return ret > 0
        return alpha > 0
    if recommendation in ("sell", "trim"):
        if alpha is None:
            return ret < 0
        return alpha < 0
    if recommendation == "hold":
        return abs(ret) <= HOLD_BAND
    return None


def _dominance(s: Session, a: Analysis) -> str:
    rec = a.recommendation
    if rec is None:
        return "balanced"
    bull_conf = _confidence_for(s, str(a.analysis_id), "bull")
    bear_conf = _confidence_for(s, str(a.analysis_id), "bear")
    if rec in ("buy", "strong_buy") and bull_conf in ("medium", "high"):
        return "bull"
    if rec in ("sell", "trim") and bear_conf in ("medium", "high"):
        return "bear"
    return "balanced"


def _confidence_for(s: Session, analysis_id: str, specialist: str) -> str | None:
    row = s.execute(
        select(AnalysisSpecialistOutput.output_json).where(
            AnalysisSpecialistOutput.analysis_id == analysis_id,
            AnalysisSpecialistOutput.specialist_name == specialist,
        )
    ).scalar_one_or_none()
    if not row or not isinstance(row, dict):
        return None
    confidence = row.get("confidence")
    return str(confidence).lower() if confidence else None
