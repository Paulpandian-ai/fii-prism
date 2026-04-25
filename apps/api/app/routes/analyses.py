"""Analyses API.

- POST /analyses               → create and start a deep-dive run
- GET  /analyses/{id}/stream   → SSE: live node-by-node progress
- GET  /analyses/{id}          → final persisted analysis + specialist outputs
- GET  /analyses?symbol=...    → history (for the Analysis History view in Section 6)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fii_agents import run_analysis, stream_analysis
from fii_db import (
    Analysis,
    AnalysisSpecialistOutput,
    AnalysisStatus,
    AnalysisType,
)
from fii_db.session import session_scope
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from app.agents_runtime import AgentsRuntime, get_runtime
from app.cost_gate import DailyCapExceeded, assert_within_daily_cap
from app.middleware.correlation import get_correlation_id

log = structlog.get_logger(__name__)

# ruff: noqa: B008  # Depends() in defaults is FastAPI's dependency-injection pattern.
router = APIRouter(prefix="/analyses", tags=["analyses"])

# Hold strong references to background tasks so they aren't GC'd mid-run.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


# --- Request / response models ------------------------------------------------------------


class CreateAnalysisRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    analysis_type: str = Field(default="deep_dive")
    # Opus 4.7 synthesis is ~5x the cost of Sonnet; off by default. UI flag in Section 6.
    use_premium_synthesis: bool = Field(default=False)
    # Only used when analysis_type == "quick_refresh": narrows specialist scope + routes
    # to Haiku 4.5.
    event_type: str | None = Field(default=None)
    event_id: str | None = Field(default=None)


class CreateAnalysisResponse(BaseModel):
    analysis_id: str
    stream_url: str


class AnalysisSummary(BaseModel):
    analysis_id: str
    symbol: str
    analysis_type: str
    status: str
    initiated_at: datetime
    completed_at: datetime | None = None
    recommendation: str | None = None
    confidence: str | None = None
    fii_score: float | None = None
    total_cost_usd: float | None = None
    action_taken: str = "none"
    action_size_usd: float | None = None
    action_price: float | None = None
    action_at: datetime | None = None
    action_notes: str | None = None


class AnalysisDetail(AnalysisSummary):
    orchestrator_summary: str | None = None
    model_calls_json: dict[str, Any] = Field(default_factory=dict)
    prompt_versions_json: dict[str, int] = Field(default_factory=dict)
    specialists: dict[str, Any] = Field(default_factory=dict)


class DecisionUpsert(BaseModel):
    action_taken: str = Field(
        pattern="^(none|bought|added|held|trimmed|sold|paper_bought|paper_sold)$"
    )
    action_size_usd: float | None = None
    action_price: float | None = None
    action_at: datetime | None = None
    action_notes: str | None = None


# --- Routes -------------------------------------------------------------------------------


@router.post("", response_model=CreateAnalysisResponse, status_code=202)
async def create_analysis(
    req: CreateAnalysisRequest, runtime: AgentsRuntime = Depends(get_runtime)
) -> CreateAnalysisResponse:
    if req.analysis_type not in ("deep_dive", "quick_refresh"):
        raise HTTPException(
            status_code=400,
            detail="analysis_type must be 'deep_dive' or 'quick_refresh'.",
        )

    # Daily spend cap pre-flight. Per-analysis cap is still enforced inside the agent loop.
    try:
        assert_within_daily_cap(runtime.session_factory)
    except DailyCapExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None

    symbol = req.symbol.upper()
    analysis_type_value = (
        AnalysisType.DEEP_DIVE.value
        if req.analysis_type == "deep_dive"
        else AnalysisType.QUICK_REFRESH.value
    )

    # Idempotency: same symbol + analysis_type + minute → reuse the existing analysis_id.
    # Guards against double-clicks and accidental retries from clients.
    idempotency_key = _idempotency_key(symbol, analysis_type_value)
    existing_id = _existing_for_key(runtime.session_factory, idempotency_key)
    if existing_id is not None:
        return CreateAnalysisResponse(
            analysis_id=existing_id,
            stream_url=f"/analyses/{existing_id}/stream",
        )

    analysis_id = str(uuid.uuid4())
    correlation_id = get_correlation_id()
    _create_pending_row(
        runtime.session_factory,
        analysis_id,
        symbol,
        analysis_type_value,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )

    extra_context: dict[str, Any] = {"use_premium_synthesis": req.use_premium_synthesis}
    if req.analysis_type == "quick_refresh":
        extra_context["model_tier"] = "haiku"
        if req.event_type:
            extra_context["quick_refresh_event_type"] = req.event_type
        if req.event_id:
            extra_context["quick_refresh_event_id"] = req.event_id
    if correlation_id:
        extra_context["correlation_id"] = correlation_id

    task = asyncio.create_task(
        _run_and_log_errors(
            runtime=runtime,
            symbol=symbol,
            analysis_id=analysis_id,
            extra_context=extra_context,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return CreateAnalysisResponse(
        analysis_id=analysis_id,
        stream_url=f"/analyses/{analysis_id}/stream",
    )


@router.get("/{analysis_id}/stream")
async def stream_analysis_events(
    analysis_id: str, runtime: AgentsRuntime = Depends(get_runtime)
) -> StreamingResponse:
    """Server-Sent Events: one `data:` line per LangGraph node completion."""
    row = _load_analysis(runtime.session_factory, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    symbol = row.symbol

    async def event_source() -> AsyncIterator[bytes]:
        yield _sse({"node": "_start", "status": "opened", "analysis_id": analysis_id})
        try:
            async for event in stream_analysis(
                symbol=symbol,
                analysis_id=analysis_id,
                user_id="00000000-0000-0000-0000-000000000000",
                database_url=runtime.database_url,
                factory=runtime.session_factory,
                embedder=runtime.embedder,
                raw_bucket=runtime.raw_bucket,
                budget=runtime.budget,
            ):
                yield _sse(event)
            yield _sse({"node": "_end", "status": "closed"})
        except Exception as exc:
            yield _sse({"node": "_error", "status": "failed", "message": str(exc)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{analysis_id}", response_model=AnalysisDetail)
async def get_analysis(
    analysis_id: str, runtime: AgentsRuntime = Depends(get_runtime)
) -> AnalysisDetail:
    row = _load_analysis(runtime.session_factory, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="analysis not found")

    with session_scope(runtime.session_factory) as s:
        specialist_rows = (
            s.execute(
                select(AnalysisSpecialistOutput).where(
                    AnalysisSpecialistOutput.analysis_id == analysis_id
                )
            )
            .scalars()
            .all()
        )
        specialists = {r.specialist_name: r.output_json for r in specialist_rows}

    return _row_to_detail(row, specialists)


@router.get("", response_model=list[AnalysisSummary])
async def list_analyses(
    symbol: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=20, ge=1, le=100),
    runtime: AgentsRuntime = Depends(get_runtime),
) -> list[AnalysisSummary]:
    with session_scope(runtime.session_factory) as s:
        stmt = select(Analysis).order_by(desc(Analysis.initiated_at)).limit(limit)
        if symbol:
            stmt = stmt.where(Analysis.symbol == symbol.upper())
        rows = s.execute(stmt).scalars().all()
    return [_row_to_summary(r) for r in rows]


@router.patch("/{analysis_id}/decision", response_model=AnalysisSummary)
async def upsert_decision(
    analysis_id: str,
    req: DecisionUpsert,
    runtime: AgentsRuntime = Depends(get_runtime),
) -> AnalysisSummary:
    """Record what the user did with this analysis. Resetting to 'none' clears the row."""
    from sqlalchemy import update as sql_update

    with session_scope(runtime.session_factory) as s:
        existing = s.execute(
            select(Analysis).where(Analysis.analysis_id == analysis_id)
        ).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        action_at = req.action_at or datetime.now(UTC)
        s.execute(
            sql_update(Analysis)
            .where(Analysis.analysis_id == analysis_id)
            .values(
                action_taken=req.action_taken,
                action_size_usd=req.action_size_usd,
                action_price=req.action_price,
                action_at=action_at if req.action_taken != "none" else None,
                action_notes=req.action_notes,
            )
        )
        refreshed = s.execute(
            select(Analysis).where(Analysis.analysis_id == analysis_id)
        ).scalar_one()
        return _row_to_summary(refreshed)


# --- Helpers ------------------------------------------------------------------------------


def _create_pending_row(
    factory: sessionmaker,
    analysis_id: str,
    symbol: str,
    analysis_type_value: str,
    *,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> None:
    with session_scope(factory) as s:
        stmt = pg_insert(Analysis).values(
            analysis_id=analysis_id,
            symbol=symbol,
            analysis_type=analysis_type_value,
            status=AnalysisStatus.PENDING.value,
            initiated_at=datetime.now(UTC),
            model_calls_json={},
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=[Analysis.analysis_id])
        s.execute(stmt)


def _idempotency_key(symbol: str, analysis_type_value: str) -> str:
    """Stable key for the same (symbol, analysis_type) within the same UTC minute.

    sha256 keeps the column at fixed length (64 chars) and is safe to store as-is.
    """
    minute = datetime.now(UTC).replace(second=0, microsecond=0).isoformat()
    raw = f"{symbol}|{analysis_type_value}|{minute}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _existing_for_key(factory: sessionmaker, key: str) -> str | None:
    """Reuse an analysis created by the same key in the past hour. Beyond the hour we
    let the user kick a fresh run rather than serving truly stale work."""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    with session_scope(factory) as s:
        row = s.execute(
            select(Analysis.analysis_id).where(
                Analysis.idempotency_key == key,
                Analysis.initiated_at >= cutoff,
            )
        ).scalar_one_or_none()
    return str(row) if row else None


def _load_analysis(factory: sessionmaker, analysis_id: str) -> Analysis | None:
    with session_scope(factory) as s:
        return s.execute(
            select(Analysis).where(Analysis.analysis_id == analysis_id)
        ).scalar_one_or_none()


def _row_to_summary(r: Analysis) -> AnalysisSummary:
    return AnalysisSummary(
        analysis_id=str(r.analysis_id),
        symbol=r.symbol,
        analysis_type=r.analysis_type,
        status=r.status,
        initiated_at=r.initiated_at,
        completed_at=r.completed_at,
        recommendation=r.recommendation,
        confidence=r.confidence,
        fii_score=float(r.fii_score) if r.fii_score is not None else None,
        total_cost_usd=float(r.total_cost_usd) if r.total_cost_usd is not None else None,
        action_taken=str(r.action_taken),
        action_size_usd=float(r.action_size_usd) if r.action_size_usd is not None else None,
        action_price=float(r.action_price) if r.action_price is not None else None,
        action_at=r.action_at,
        action_notes=r.action_notes,
    )


def _row_to_detail(r: Analysis, specialists: dict[str, Any]) -> AnalysisDetail:
    return AnalysisDetail(
        analysis_id=str(r.analysis_id),
        symbol=r.symbol,
        analysis_type=r.analysis_type,
        status=r.status,
        initiated_at=r.initiated_at,
        completed_at=r.completed_at,
        recommendation=r.recommendation,
        confidence=r.confidence,
        fii_score=float(r.fii_score) if r.fii_score is not None else None,
        total_cost_usd=float(r.total_cost_usd) if r.total_cost_usd is not None else None,
        orchestrator_summary=r.orchestrator_summary,
        model_calls_json=r.model_calls_json or {},
        prompt_versions_json=r.prompt_versions_json or {},
        specialists=specialists,
        action_taken=str(r.action_taken),
        action_size_usd=float(r.action_size_usd) if r.action_size_usd is not None else None,
        action_price=float(r.action_price) if r.action_price is not None else None,
        action_at=r.action_at,
        action_notes=r.action_notes,
    )


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode()


async def _run_and_log_errors(
    *,
    runtime: AgentsRuntime,
    symbol: str,
    analysis_id: str,
    extra_context: dict[str, Any] | None = None,
) -> None:
    try:
        await run_analysis(
            symbol=symbol,
            analysis_id=analysis_id,
            user_id="00000000-0000-0000-0000-000000000000",
            database_url=runtime.database_url,
            factory=runtime.session_factory,
            embedder=runtime.embedder,
            raw_bucket=runtime.raw_bucket,
            budget=runtime.budget,
            extra_context=extra_context or {},
        )
    except Exception:
        log.exception("background_run_failed", analysis_id=analysis_id, symbol=symbol)
