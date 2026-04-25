"""Decision journal aggregations.

- GET /journal              → summary stats (decision count, hit rates, alpha)
- GET /journal/decisions    → table of decisions with their outcomes
- GET /journal/breakdowns   → pivot by recommendation, confidence, dominance, prompt-version
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query
from fii_db import Analysis, AnalysisOutcome
from fii_db.session import session_scope
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.agents_runtime import AgentsRuntime, get_runtime

log = structlog.get_logger(__name__)

# ruff: noqa: B008
router = APIRouter(prefix="/journal", tags=["journal"])


HORIZONS = ("1d", "1w", "1m", "3m", "6m", "1y")


# --- Response shapes ----------------------------------------------------------------------


class JournalSummary(BaseModel):
    decision_count: int
    paper_count: int
    real_count: int
    avg_alpha_by_horizon: dict[str, float | None]
    hit_rate_by_horizon: dict[str, float | None]
    win_loss_by_horizon: dict[str, dict[str, int]]


class DecisionRow(BaseModel):
    analysis_id: str
    symbol: str
    recommendation: str | None
    confidence: str | None
    fii_score: float | None
    action_taken: str
    action_size_usd: float | None
    action_price: float | None
    action_at: datetime | None
    return_1m: float | None = None
    return_3m: float | None = None
    alpha_1m: float | None = None
    alpha_3m: float | None = None
    hit_1m: bool | None = None
    hit_3m: bool | None = None
    dominance: str | None = None


class BreakdownBucket(BaseModel):
    label: str
    count: int
    avg_alpha_1m: float | None
    avg_alpha_3m: float | None
    hit_rate_1m: float | None
    hit_rate_3m: float | None


class BreakdownsResponse(BaseModel):
    by_recommendation: list[BreakdownBucket] = Field(default_factory=list)
    by_confidence: list[BreakdownBucket] = Field(default_factory=list)
    by_dominance: list[BreakdownBucket] = Field(default_factory=list)
    by_prompt_version: list[BreakdownBucket] = Field(default_factory=list)


# --- Routes -------------------------------------------------------------------------------


@router.get("", response_model=JournalSummary)
async def get_summary(runtime: AgentsRuntime = Depends(get_runtime)) -> JournalSummary:
    decisions = _journaled(runtime)
    real_count = sum(
        1 for d in decisions if d["action_taken"] not in ("none", "paper_bought", "paper_sold")
    )
    paper_count = sum(1 for d in decisions if d["action_taken"] in ("paper_bought", "paper_sold"))

    avg_alpha = {h: _avg(d.get(f"alpha_{h}") for d in decisions) for h in HORIZONS}
    hit_rate = {h: _hit_rate(d.get(f"hit_{h}") for d in decisions) for h in HORIZONS}
    win_loss = {h: _win_loss_bucket(d.get(f"hit_{h}") for d in decisions) for h in HORIZONS}

    return JournalSummary(
        decision_count=len(decisions),
        paper_count=paper_count,
        real_count=real_count,
        avg_alpha_by_horizon=avg_alpha,
        hit_rate_by_horizon=hit_rate,
        win_loss_by_horizon=win_loss,
    )


@router.get("/decisions", response_model=list[DecisionRow])
async def get_decisions(
    limit: int = Query(default=200, ge=1, le=1000),
    runtime: AgentsRuntime = Depends(get_runtime),
) -> list[DecisionRow]:
    decisions = _journaled(runtime, limit=limit)
    return [DecisionRow(**d) for d in decisions]


@router.get("/breakdowns", response_model=BreakdownsResponse)
async def get_breakdowns(
    runtime: AgentsRuntime = Depends(get_runtime),
) -> BreakdownsResponse:
    decisions = _journaled(runtime)
    return BreakdownsResponse(
        by_recommendation=_bucket(
            decisions,
            key=lambda d: d.get("recommendation") or "unknown",
        ),
        by_confidence=_bucket(decisions, key=lambda d: d.get("confidence") or "unknown"),
        by_dominance=_bucket(decisions, key=lambda d: d.get("dominance") or "unknown"),
        by_prompt_version=_bucket(decisions, key=_prompt_version_key),
    )


# --- Helpers ------------------------------------------------------------------------------


def _journaled(runtime: AgentsRuntime, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return a flat list of dicts joining Analysis + AnalysisOutcome for journaled rows."""
    with session_scope(runtime.session_factory) as s:
        stmt = (
            select(Analysis, AnalysisOutcome)
            .where(Analysis.action_taken != "none")
            .outerjoin(AnalysisOutcome, AnalysisOutcome.analysis_id == Analysis.analysis_id)
            .order_by(desc(Analysis.action_at))
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = s.execute(stmt).all()
    out: list[dict[str, Any]] = []
    for analysis, outcome in rows:
        d = {
            "analysis_id": str(analysis.analysis_id),
            "symbol": analysis.symbol,
            "recommendation": analysis.recommendation,
            "confidence": analysis.confidence,
            "fii_score": float(analysis.fii_score) if analysis.fii_score is not None else None,
            "action_taken": str(analysis.action_taken),
            "action_size_usd": (
                float(analysis.action_size_usd) if analysis.action_size_usd is not None else None
            ),
            "action_price": (
                float(analysis.action_price) if analysis.action_price is not None else None
            ),
            "action_at": analysis.action_at,
            "prompt_versions_json": analysis.prompt_versions_json or {},
        }
        if outcome is not None:
            for h in HORIZONS:
                d[f"return_{h}"] = (
                    float(getattr(outcome, f"return_{h}"))
                    if getattr(outcome, f"return_{h}") is not None
                    else None
                )
                d[f"alpha_{h}"] = (
                    float(getattr(outcome, f"alpha_{h}"))
                    if getattr(outcome, f"alpha_{h}") is not None
                    else None
                )
                d[f"hit_{h}"] = getattr(outcome, f"hit_{h}")
            d["dominance"] = outcome.dominance
        out.append(d)
    return out


def _avg(values: Iterable[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _hit_rate(values: Iterable[bool | None]) -> float | None:
    decided = [v for v in values if v is not None]
    if not decided:
        return None
    return sum(1 for v in decided if v) / len(decided)


def _win_loss_bucket(values: Iterable[bool | None]) -> dict[str, int]:
    wins = losses = pending = 0
    for v in values:
        if v is True:
            wins += 1
        elif v is False:
            losses += 1
        else:
            pending += 1
    return {"wins": wins, "losses": losses, "pending": pending}


def _bucket(decisions: list[dict[str, Any]], *, key) -> list[BreakdownBucket]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in decisions:
        k = key(d)
        groups.setdefault(k, []).append(d)
    out: list[BreakdownBucket] = []
    for label, ds in sorted(groups.items()):
        out.append(
            BreakdownBucket(
                label=label,
                count=len(ds),
                avg_alpha_1m=_avg(d.get("alpha_1m") for d in ds),
                avg_alpha_3m=_avg(d.get("alpha_3m") for d in ds),
                hit_rate_1m=_hit_rate(d.get("hit_1m") for d in ds),
                hit_rate_3m=_hit_rate(d.get("hit_3m") for d in ds),
            )
        )
    return out


def _prompt_version_key(d: dict[str, Any]) -> str:
    """Compact label like 'fund=1,val=1,moat=2' so dashboards can group by exact prompt
    bundle. Specialists not in the dict get '?'."""
    pv = d.get("prompt_versions_json") or {}
    if not pv:
        return "no_versions"
    parts = []
    for short, key in (
        ("fund", "fundamentals"),
        ("val", "valuation"),
        ("moat", "moat"),
        ("macro", "macro"),
        ("tech", "technical"),
        ("news", "news"),
        ("ins", "insider"),
        ("risk", "risk"),
        ("bull", "bull"),
        ("bear", "bear"),
    ):
        v = pv.get(key)
        parts.append(f"{short}={v if v is not None else '?'}")
    return ",".join(parts)
