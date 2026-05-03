"""Per-specialist run-on-demand API.

Replaces the "POST /analyses fans out to all 10 specialists" model with four
narrow endpoints rooted at /stocks/{symbol}:

- GET  /stocks/{symbol}/specialists                  → list cache rows + freshness
- POST /stocks/{symbol}/specialists/{name}           → run one specialist (force=)
- POST /stocks/{symbol}/synthesize                   → run synthesis from cache
- GET  /stocks/{symbol}/total-spend                  → cumulative spend per symbol

The legacy /analyses routes still work for backwards compat and will be migrated
in Phase 2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fii_agents.cache_policy import CACHEABLE_SPECIALISTS, canonical_name
from fii_agents.specialist_runner import (
    list_cached_views,
    run_specialist,
    total_spend_usd,
)
from fii_agents.synthesis_runner import MissingSpecialists, synthesize_from_cache
from pydantic import BaseModel, Field

from app.agents_runtime import AgentsRuntime, get_runtime
from app.cost_gate import DailyCapExceeded, assert_within_daily_cap

log = structlog.get_logger(__name__)

# ruff: noqa: B008  # Depends() in defaults is FastAPI's dependency-injection pattern.
router = APIRouter(prefix="/stocks", tags=["stocks"])


# --- Request / response models -----------------------------------------------------------


class CachedSpecialistView(BaseModel):
    name: str
    state: str  # 'fresh' | 'stale' | 'missing'
    status: str | None
    last_run_at: datetime | None
    expires_at: datetime | None
    cost_usd: float
    has_output: bool
    # Full cached payload included so the "View output" modal in the UI doesn't
    # need a second round-trip. Total payload at 8 specialists × ~5KB ≈ 40KB
    # which is fine; if it grows we can introduce a per-specialist GET endpoint.
    output: dict[str, Any] | None = None


class RunSpecialistRequest(BaseModel):
    force: bool = Field(default=False)
    model_tier: str = Field(default="sonnet", pattern="^(sonnet|haiku)$")


class RunSpecialistResponse(BaseModel):
    name: str
    symbol: str
    status: str  # 'ok' | 'aborted_cap' | 'error'
    from_cache: bool
    last_run_at: datetime
    expires_at: datetime
    cost_usd: float
    tokens_in: int
    tokens_out: int
    duration_ms: int
    model_used: str | None
    output: dict[str, Any] | None
    error: str | None = None


class SynthesizeRequest(BaseModel):
    use_premium: bool = Field(default=False)


class SynthesizeResponse(BaseModel):
    analysis_id: str
    symbol: str
    status: str  # 'ok' | 'synthesis_invalid'
    final: dict[str, Any]
    cost_usd: float
    tokens_in: int
    tokens_out: int
    duration_ms: int


class TotalSpendResponse(BaseModel):
    symbol: str
    specialist_spend_usd: float
    synthesis_spend_usd: float
    total_spend_usd: float
    specialist_rows: int
    analyses_rows: int


# --- Routes ------------------------------------------------------------------------------


@router.get("/{symbol}/specialists", response_model=list[CachedSpecialistView])
async def list_specialists(
    symbol: str, runtime: AgentsRuntime = Depends(get_runtime)
) -> list[CachedSpecialistView]:
    sym = symbol.upper()
    views = list_cached_views(runtime.session_factory, sym)
    return [
        CachedSpecialistView(
            name=v.name,
            state=v.state,
            status=v.status,
            last_run_at=v.last_run_at,
            expires_at=v.expires_at,
            cost_usd=v.cost_usd,
            has_output=v.output is not None,
            output=v.output,
        )
        for v in views
    ]


@router.post("/{symbol}/specialists/{name}", response_model=RunSpecialistResponse)
async def run_one_specialist(
    symbol: str,
    name: str,
    req: RunSpecialistRequest,
    runtime: AgentsRuntime = Depends(get_runtime),
) -> RunSpecialistResponse:
    canon = canonical_name(name)
    if canon not in CACHEABLE_SPECIALISTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown specialist {name!r}; must be one of "
                f"{sorted(CACHEABLE_SPECIALISTS)}"
            ),
        )
    try:
        assert_within_daily_cap(runtime.session_factory)
    except DailyCapExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None

    result = await run_specialist(
        canon,
        symbol=symbol,
        factory=runtime.session_factory,
        embedder=runtime.embedder,
        raw_bucket=runtime.raw_bucket,
        force=req.force,
        model_tier=req.model_tier,
    )
    return RunSpecialistResponse(
        name=result.name,
        symbol=result.symbol,
        status=result.status,
        from_cache=result.from_cache,
        last_run_at=result.last_run_at,
        expires_at=result.expires_at,
        cost_usd=result.cost_usd,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        duration_ms=result.duration_ms,
        model_used=result.model_used,
        output=result.output,
        error=result.error,
    )


@router.post("/{symbol}/synthesize", response_model=SynthesizeResponse)
async def synthesize(
    symbol: str,
    req: SynthesizeRequest,
    runtime: AgentsRuntime = Depends(get_runtime),
) -> SynthesizeResponse:
    try:
        assert_within_daily_cap(runtime.session_factory)
    except DailyCapExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None

    try:
        result = await synthesize_from_cache(
            symbol,
            factory=runtime.session_factory,
            embedder=runtime.embedder,
            raw_bucket=runtime.raw_bucket,
            use_premium=req.use_premium,
        )
    except MissingSpecialists as exc:
        # 400 with the missing names so the UI can prompt the user to run them.
        raise HTTPException(
            status_code=400,
            detail={
                "error": "specialists_not_ready",
                "message": str(exc),
                "missing": exc.missing,
                "stale": exc.stale,
            },
        ) from None

    return SynthesizeResponse(
        analysis_id=result.analysis_id,
        symbol=result.symbol,
        status=result.status,
        final=result.final,
        cost_usd=result.cost_usd,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        duration_ms=result.duration_ms,
    )


@router.get("/{symbol}/total-spend", response_model=TotalSpendResponse)
async def get_total_spend(
    symbol: str, runtime: AgentsRuntime = Depends(get_runtime)
) -> TotalSpendResponse:
    spend = total_spend_usd(runtime.session_factory, symbol)
    return TotalSpendResponse(**spend)
