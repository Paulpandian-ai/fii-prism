"""Analyses API.

- POST /analyses               → create and start a deep-dive run
- GET  /analyses/{id}/stream   → SSE: live node-by-node progress
- GET  /analyses/{id}          → final persisted analysis + specialist outputs
- GET  /analyses?symbol=...    → history (for the Analysis History view in Section 6)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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

log = structlog.get_logger(__name__)

# ruff: noqa: B008  # Depends() in defaults is FastAPI's dependency-injection pattern.
router = APIRouter(prefix="/analyses", tags=["analyses"])

# Hold strong references to background tasks so they aren't GC'd mid-run.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


# --- Request / response models ------------------------------------------------------------


class CreateAnalysisRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    analysis_type: str = Field(default="deep_dive")


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


class AnalysisDetail(AnalysisSummary):
    orchestrator_summary: str | None = None
    model_calls_json: dict[str, Any] = Field(default_factory=dict)
    specialists: dict[str, Any] = Field(default_factory=dict)


# --- Routes -------------------------------------------------------------------------------


@router.post("", response_model=CreateAnalysisResponse, status_code=202)
async def create_analysis(
    req: CreateAnalysisRequest, runtime: AgentsRuntime = Depends(get_runtime)
) -> CreateAnalysisResponse:
    if req.analysis_type != "deep_dive":
        raise HTTPException(
            status_code=400, detail="Only analysis_type='deep_dive' is supported in Section 4."
        )
    analysis_id = str(uuid.uuid4())
    symbol = req.symbol.upper()

    # Pre-create the Analysis row so the stream endpoint can find it even if the
    # background run hasn't persisted anything yet.
    _create_pending_row(runtime.session_factory, analysis_id, symbol)

    # Kick off the run as a background task. The stream endpoint reads events from
    # a fresh astream in the SAME orchestrator pipeline (LangGraph replays from the
    # checkpoint if the work already completed between POST and GET).
    task = asyncio.create_task(
        _run_and_log_errors(runtime=runtime, symbol=symbol, analysis_id=analysis_id)
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


# --- Helpers ------------------------------------------------------------------------------


def _create_pending_row(factory: sessionmaker, analysis_id: str, symbol: str) -> None:
    with session_scope(factory) as s:
        stmt = pg_insert(Analysis).values(
            analysis_id=analysis_id,
            symbol=symbol,
            analysis_type=AnalysisType.DEEP_DIVE.value,
            status=AnalysisStatus.PENDING.value,
            initiated_at=datetime.now(UTC),
            model_calls_json={},
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=[Analysis.analysis_id])
        s.execute(stmt)


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
        specialists=specialists,
    )


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode()


async def _run_and_log_errors(*, runtime: AgentsRuntime, symbol: str, analysis_id: str) -> None:
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
        )
    except Exception:
        log.exception("background_run_failed", analysis_id=analysis_id, symbol=symbol)
