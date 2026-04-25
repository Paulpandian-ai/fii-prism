"""Admin observability endpoints.

- GET /admin/stats           → analyses/day, avg cost, error rate by specialist, token use
- GET /admin/circuit-breakers → snapshot of every per-provider breaker
- GET /admin/cost-cap        → today's spend vs daily cap
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from fii_data_clients import BaseHttpClient
from fii_db import Analysis, AnalysisSpecialistOutput
from fii_db.session import session_scope
from pydantic import BaseModel
from sqlalchemy import case, func, select

from app.agents_runtime import AgentsRuntime, get_runtime
from app.cost_gate import cap_status

log = structlog.get_logger(__name__)

# ruff: noqa: B008
router = APIRouter(prefix="/admin", tags=["admin"])


# --- Response shapes ----------------------------------------------------------------------


class DailyVolume(BaseModel):
    date: str
    analyses: int
    avg_cost_usd: float | None
    total_cost_usd: float


class SpecialistHealth(BaseModel):
    specialist: str
    runs: int
    avg_duration_ms: float | None
    error_rate: float


class TokenUsageRow(BaseModel):
    model: str | None
    runs: int
    tokens_in: int
    tokens_out: int


class AdminStats(BaseModel):
    daily_volume: list[DailyVolume]
    specialist_health: list[SpecialistHealth]
    token_usage: list[TokenUsageRow]


class CircuitSnapshot(BaseModel):
    name: str
    state: str
    consecutive_failures: int
    cooldown_remaining_s: float


# --- Routes -------------------------------------------------------------------------------


@router.get("/stats", response_model=AdminStats)
async def get_stats(runtime: AgentsRuntime = Depends(get_runtime)) -> AdminStats:
    cutoff = datetime.now(UTC) - timedelta(days=14)
    with session_scope(runtime.session_factory) as s:
        # Daily volume + cost over the last 14 days.
        date_col = func.date(Analysis.initiated_at).label("d")
        rows = s.execute(
            select(
                date_col,
                func.count(Analysis.analysis_id),
                func.avg(Analysis.total_cost_usd),
                func.coalesce(func.sum(Analysis.total_cost_usd), 0.0),
            )
            .where(Analysis.initiated_at >= cutoff)
            .group_by(date_col)
            .order_by(date_col)
        ).all()
        daily = [
            DailyVolume(
                date=str(d),
                analyses=int(n),
                avg_cost_usd=float(avg) if avg is not None else None,
                total_cost_usd=float(total or 0.0),
            )
            for d, n, avg, total in rows
        ]

        # Per-specialist error rate. We treat an empty output_json as a failed run.
        is_error = case((AnalysisSpecialistOutput.output_json == {}, 1), else_=0)
        spec_rows = s.execute(
            select(
                AnalysisSpecialistOutput.specialist_name,
                func.count(AnalysisSpecialistOutput.output_id),
                func.avg(AnalysisSpecialistOutput.duration_ms),
                func.sum(is_error),
            )
            .group_by(AnalysisSpecialistOutput.specialist_name)
            .order_by(AnalysisSpecialistOutput.specialist_name)
        ).all()
        spec_health = [
            SpecialistHealth(
                specialist=name,
                runs=int(n),
                avg_duration_ms=float(avg_dur) if avg_dur is not None else None,
                error_rate=(float(errors) / float(n)) if n else 0.0,
            )
            for name, n, avg_dur, errors in spec_rows
        ]

        # Token usage by model. Pulls from specialist outputs since that's where model_used lives.
        token_rows = s.execute(
            select(
                AnalysisSpecialistOutput.model_used,
                func.count(AnalysisSpecialistOutput.output_id),
                func.coalesce(func.sum(AnalysisSpecialistOutput.tokens_in), 0),
                func.coalesce(func.sum(AnalysisSpecialistOutput.tokens_out), 0),
            ).group_by(AnalysisSpecialistOutput.model_used)
        ).all()
        token_usage = [
            TokenUsageRow(
                model=model,
                runs=int(n),
                tokens_in=int(t_in or 0),
                tokens_out=int(t_out or 0),
            )
            for model, n, t_in, t_out in token_rows
        ]

    return AdminStats(
        daily_volume=daily,
        specialist_health=spec_health,
        token_usage=token_usage,
    )


@router.get("/circuit-breakers", response_model=list[CircuitSnapshot])
async def get_breakers() -> list[CircuitSnapshot]:
    return [CircuitSnapshot(**snap) for snap in BaseHttpClient.all_breaker_snapshots()]


@router.get("/cost-cap")
async def get_cost_cap(runtime: AgentsRuntime = Depends(get_runtime)) -> dict[str, Any]:
    return cap_status(runtime.session_factory)
