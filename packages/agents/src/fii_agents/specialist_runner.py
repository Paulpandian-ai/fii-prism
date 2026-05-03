"""Per-specialist run-on-demand with caching.

This is the new entry point for running a *single* specialist for a symbol. It
replaces the LangGraph fan-out as the canonical way analyses are produced.

Lifecycle of `run_specialist(name, symbol, ...)`:
  1. Look up `specialist_cache[(symbol, name)]`. If present and fresh (status='ok'
     and now - last_run_at < TTL), return the cached output without invoking Claude.
  2. Otherwise instantiate the right Specialist class, run it (per-specialist
     call/cost caps enforced inside the loop), and UPSERT the result into
     `specialist_cache` under the same (symbol, name) key.
  3. Return a `SpecialistRunResult` with both the freshness state and the actual
     output (or None on cap-aborted runs).

The `force=True` flag bypasses the cache lookup and always re-runs.

Bull/bear/synthesis are NOT supported here — they run inside synthesis as a small
LangGraph (see `specialists/synthesis.py`).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from fii_data_clients import Embedder
from fii_db import SpecialistCache, Ticker
from fii_db.session import session_scope
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from fii_agents.cache_policy import (
    CACHEABLE_SPECIALISTS,
    canonical_name,
    expires_at,
    is_fresh,
    ttl_seconds,
)
from fii_agents.model import MODEL_HAIKU, MODEL_SONNET, Model
from fii_agents.specialists.base import Specialist, SpecialistContext, SpecialistResult
from fii_agents.specialists.fundamentals import FundamentalsSpecialist
from fii_agents.specialists.insider import InsiderFlowSpecialist
from fii_agents.specialists.macro import MacroSpecialist
from fii_agents.specialists.moat import MoatSpecialist
from fii_agents.specialists.news import NewsSentimentSpecialist
from fii_agents.specialists.risk import RiskSpecialist
from fii_agents.specialists.technical import TechnicalSpecialist
from fii_agents.specialists.valuation import ValuationSpecialist

log = structlog.get_logger(__name__)


# --- Specialist registry -----------------------------------------------------------------

# Canonical name -> Specialist class. Bull/bear/synthesis intentionally absent.
_REGISTRY: Mapping[str, type[Specialist]] = {
    "fundamentals": FundamentalsSpecialist,
    "valuation": ValuationSpecialist,
    "moat": MoatSpecialist,
    "macro": MacroSpecialist,
    "technical": TechnicalSpecialist,
    "news": NewsSentimentSpecialist,
    "insider": InsiderFlowSpecialist,
    "risk": RiskSpecialist,
}


# --- Public API --------------------------------------------------------------------------


@dataclass
class SpecialistRunResult:
    """Returned by `run_specialist`. Distinct from SpecialistResult so callers can
    see whether the result came from cache + the freshness expiry."""

    name: str
    symbol: str
    output: dict[str, Any] | None
    status: str  # 'ok' | 'aborted_cap' | 'error'
    from_cache: bool
    last_run_at: datetime
    expires_at: datetime
    cost_usd: float
    tokens_in: int
    tokens_out: int
    duration_ms: int
    model_used: str | None
    error: str | None = None


@dataclass
class CachedView:
    """Lightweight read of one cache row. `state` is one of fresh/stale/missing."""

    name: str
    symbol: str
    state: str  # 'fresh' | 'stale' | 'missing'
    status: str | None  # None when missing
    last_run_at: datetime | None
    expires_at: datetime | None
    cost_usd: float
    output: dict[str, Any] | None


def list_cached_views(factory: sessionmaker, symbol: str) -> list[CachedView]:
    """Return one row per cacheable specialist with its current freshness state."""
    sym = symbol.upper()
    by_name: dict[str, CachedView] = {}
    with session_scope(factory) as s:
        rows = (
            s.execute(select(SpecialistCache).where(SpecialistCache.symbol == sym))
            .scalars()
            .all()
        )
        for r in rows:
            state = (
                "fresh"
                if is_fresh(r.specialist_name, r.last_run_at, status=r.status)
                else "stale"
            )
            by_name[r.specialist_name] = CachedView(
                name=r.specialist_name,
                symbol=sym,
                state=state,
                status=r.status,
                last_run_at=r.last_run_at,
                expires_at=expires_at(r.specialist_name, r.last_run_at),
                cost_usd=float(r.cost_usd),
                output=r.output_json,
            )
    out: list[CachedView] = []
    for name in CACHEABLE_SPECIALISTS:
        if name in by_name:
            out.append(by_name[name])
        else:
            out.append(
                CachedView(
                    name=name,
                    symbol=sym,
                    state="missing",
                    status=None,
                    last_run_at=None,
                    expires_at=None,
                    cost_usd=0.0,
                    output=None,
                )
            )
    return out


def total_spend_usd(factory: sessionmaker, symbol: str) -> dict[str, Any]:
    """Sum the cumulative spend for `symbol` across the specialist cache + the
    `analyses.total_cost_usd` history (synthesis runs)."""
    from fii_db import Analysis
    from sqlalchemy import func

    sym = symbol.upper()
    with session_scope(factory) as s:
        spec_total = s.execute(
            select(func.coalesce(func.sum(SpecialistCache.cost_usd), 0)).where(
                SpecialistCache.symbol == sym
            )
        ).scalar_one()
        synth_total = s.execute(
            select(func.coalesce(func.sum(Analysis.total_cost_usd), 0)).where(
                Analysis.symbol == sym
            )
        ).scalar_one()
        spec_runs = s.execute(
            select(func.count()).select_from(SpecialistCache).where(SpecialistCache.symbol == sym)
        ).scalar_one()
        synth_runs = s.execute(
            select(func.count()).select_from(Analysis).where(Analysis.symbol == sym)
        ).scalar_one()
    return {
        "symbol": sym,
        "specialist_spend_usd": float(spec_total or 0),
        "synthesis_spend_usd": float(synth_total or 0),
        "total_spend_usd": float((spec_total or 0) + (synth_total or 0)),
        "specialist_rows": int(spec_runs or 0),
        "analyses_rows": int(synth_runs or 0),
    }


async def run_specialist(
    name: str,
    *,
    symbol: str,
    factory: sessionmaker,
    embedder: Embedder,
    raw_bucket: str | None = None,
    user_id: str = "00000000-0000-0000-0000-000000000000",
    analysis_id: str | None = None,
    context: dict[str, Any] | None = None,
    force: bool = False,
    model_tier: str = "sonnet",
) -> SpecialistRunResult:
    """Run a single specialist for a symbol with cache.

    `force=True` bypasses the cache lookup. `model_tier="haiku"` routes to Haiku 4.5
    for cheap quick-refresh runs; default is Sonnet 4.6.
    """
    canon = canonical_name(name)
    if canon not in _REGISTRY:
        raise ValueError(
            f"unknown specialist {name!r}; must be one of {sorted(_REGISTRY)}"
        )

    sym = symbol.upper()

    # Ensure the ticker FK is satisfied so the cache row can be written even if
    # the seed hasn't run.
    _ensure_ticker(factory, sym)

    # Cache hit fast path.
    if not force:
        with session_scope(factory) as s:
            cached = s.execute(
                select(SpecialistCache).where(
                    SpecialistCache.symbol == sym,
                    SpecialistCache.specialist_name == canon,
                )
            ).scalar_one_or_none()
        if cached is not None and is_fresh(canon, cached.last_run_at, status=cached.status):
            log.info("specialist_cache_hit", specialist=canon, symbol=sym)
            return SpecialistRunResult(
                name=canon,
                symbol=sym,
                output=cached.output_json,
                status=cached.status,
                from_cache=True,
                last_run_at=cached.last_run_at,
                expires_at=expires_at(canon, cached.last_run_at),
                cost_usd=float(cached.cost_usd),
                tokens_in=cached.tokens_in,
                tokens_out=cached.tokens_out,
                duration_ms=cached.duration_ms,
                model_used=cached.model_used,
            )

    # Cache miss / stale / forced — actually run.
    spec = _REGISTRY[canon]()
    ctx = SpecialistContext(
        symbol=sym,
        analysis_id=analysis_id or "00000000-0000-0000-0000-000000000000",
        user_id=user_id,
        factory=factory,
        embedder=embedder,
        raw_bucket=raw_bucket,
        context=context or {},
    )
    model_id = MODEL_HAIKU if model_tier == "haiku" else MODEL_SONNET
    model = Model(model_id=model_id)

    started = time.perf_counter()
    try:
        result: SpecialistResult = await spec.run(ctx, model)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.exception("specialist_run_exception", specialist=canon, symbol=sym)
        # Persist the failure as an aborted_cap row so synthesis sees the partial
        # state and the operator can see what happened.
        now = datetime.now(UTC)
        _upsert_cache(
            factory,
            sym,
            canon,
            output_json={},
            reasoning_text=str(exc)[:500],
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            duration_ms=duration_ms,
            status="error",
            model_used=model_id,
            last_run_at=now,
        )
        return SpecialistRunResult(
            name=canon,
            symbol=sym,
            output=None,
            status="error",
            from_cache=False,
            last_run_at=now,
            expires_at=expires_at(canon, now),
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            duration_ms=duration_ms,
            model_used=model_id,
            error=str(exc)[:500],
        )

    output_json: dict[str, Any] = (
        result.output.model_dump(mode="json") if result.output is not None else {}
    )
    now = datetime.now(UTC)
    _upsert_cache(
        factory,
        sym,
        canon,
        output_json=output_json,
        reasoning_text=None,
        cost_usd=result.cost_usd,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        duration_ms=result.duration_ms,
        status=result.status,
        model_used=model_id if not model.is_fake else "fake",
        last_run_at=now,
    )
    log.info(
        "specialist_persisted",
        specialist=canon,
        symbol=sym,
        status=result.status,
        cost_usd=round(result.cost_usd, 6),
        from_cache=False,
    )
    return SpecialistRunResult(
        name=canon,
        symbol=sym,
        output=output_json or None,
        status=result.status,
        from_cache=False,
        last_run_at=now,
        expires_at=expires_at(canon, now),
        cost_usd=result.cost_usd,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        duration_ms=result.duration_ms,
        model_used=model_id if not model.is_fake else "fake",
        error=result.error,
    )


# --- Helpers -----------------------------------------------------------------------------


def _ensure_ticker(factory: sessionmaker, symbol: str) -> None:
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )


def _input_hash(symbol: str) -> str:
    """Phase-1 plumbing: regenerated-this-UTC-day fingerprint. Phase 2 will fold in
    the latest price / fundamentals ingestion timestamps so a fresh data load
    invalidates without waiting on the TTL."""
    digest = hashlib.sha256(f"{symbol}|{date.today().isoformat()}".encode()).hexdigest()
    return digest[:32]


def _upsert_cache(
    factory: sessionmaker,
    symbol: str,
    name: str,
    *,
    output_json: dict[str, Any],
    reasoning_text: str | None,
    cost_usd: float,
    tokens_in: int,
    tokens_out: int,
    duration_ms: int,
    status: str,
    model_used: str | None,
    last_run_at: datetime | None = None,
) -> None:
    values = {
        "symbol": symbol,
        "specialist_name": name,
        "output_json": output_json,
        "reasoning_text": reasoning_text,
        "citations_json": {},
        "model_used": model_used,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "cost_usd": Decimal(str(round(cost_usd, 4))),
        "duration_ms": int(duration_ms),
        "status": status,
        "last_run_at": last_run_at or datetime.now(UTC),
        "last_input_hash": _input_hash(symbol),
    }
    stmt = pg_insert(SpecialistCache).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SpecialistCache.symbol, SpecialistCache.specialist_name],
        set_={k: v for k, v in values.items() if k not in ("symbol", "specialist_name")},
    )
    with session_scope(factory) as s:
        s.execute(stmt)


# Synthesis-result persistence + freshness gate live next to the orchestrator since
# they read multiple cache rows. See `synthesis_runner.py`.
TTL_FOR = ttl_seconds  # convenience re-export for callers
