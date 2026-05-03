"""Synthesize a final analysis from the per-specialist cache.

This is the new "entry point" that replaces the LangGraph fan-out as the canonical
way analyses are produced. The 8 cacheable specialists (fundamentals, valuation,
moat, macro, technical, news, insider, risk) MUST be present and fresh in
`specialist_cache` before synthesis can run. Bull/bear/synthesis run here as a
sequential mini-pipeline (not exposed as standalone endpoints) since they only
add value once every primary specialist is in place.

Flow of `synthesize_from_cache(symbol, ...)`:
  1. Read all 8 cache rows for the symbol. Refuse with `MissingSpecialists` if
     any are missing or stale (status != 'ok' OR past their TTL).
  2. Project the cached outputs into the AnalysisState shape that bull/bear/
     synthesis expect (`news_sentiment`, `insider_flow`, `risk` keys).
  3. Run BullResearcher, BearResearcher, RiskSpecialist (final pass with bull/bear
     priors), then `run_synthesis`. Each call respects its own caps; the synthesis
     attempt-cap (`synthesis_max_attempts()`, default 3) lives inside `run_synthesis`.
  4. Persist a single Analysis row + per-specialist rows + return the result. The
     status is `'synthesis_invalid'` if synthesis exhausted its attempt cap.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from fii_data_clients import Embedder
from fii_db import (
    Analysis,
    AnalysisSpecialistOutput,
    AnalysisStatus,
    AnalysisType,
    SpecialistCache,
    SpecialistName,
)
from fii_db.session import session_scope
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from fii_agents.cache_policy import CACHEABLE_SPECIALISTS, is_fresh
from fii_agents.model import MODEL_OPUS, MODEL_SONNET, Model
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.specialists.debate import BearResearcher, BullResearcher
from fii_agents.specialists.risk import RiskSpecialist
from fii_agents.specialists.synthesis import run_synthesis

log = structlog.get_logger(__name__)


# Cache-key (canonical) -> AnalysisState key. The state keys are the names the
# Bull/Bear/Risk-final/Synthesis prompts already understand.
_STATE_KEY_FROM_CACHE: dict[str, str] = {
    "fundamentals": "fundamentals",
    "valuation": "valuation",
    "moat": "moat",
    "macro": "macro",
    "technical": "technical",
    "news": "news_sentiment",
    "insider": "insider_flow",
    "risk": "risk_preliminary",
}

# Cache-key -> SpecialistName enum for persisting per-specialist rows.
_SPECIALIST_ENUM: dict[str, SpecialistName] = {
    "fundamentals": SpecialistName.FUNDAMENTALS,
    "valuation": SpecialistName.VALUATION,
    "moat": SpecialistName.MOAT,
    "macro": SpecialistName.MACRO,
    "technical": SpecialistName.TECHNICAL,
    "news": SpecialistName.NEWS,
    "insider": SpecialistName.INSIDER,
    "risk": SpecialistName.RISK,
}


# --- Errors ------------------------------------------------------------------------------


class MissingSpecialists(Exception):
    """Raised when synthesize_from_cache cannot proceed because one or more
    required specialists are missing/stale. The caller (API layer) translates this
    into a 400 with the missing names in the body."""

    def __init__(self, missing: list[str], stale: list[str] | None = None) -> None:
        self.missing = sorted(missing)
        self.stale = sorted(stale or [])
        all_blocked = self.missing + self.stale
        super().__init__(
            f"synthesis blocked: {len(all_blocked)} specialist(s) "
            f"need a (re-)run before synthesis: {all_blocked}"
        )


# --- Result ------------------------------------------------------------------------------


@dataclass
class SynthesisRunResult:
    analysis_id: str
    symbol: str
    status: str  # 'ok' | 'synthesis_invalid' | 'error'
    final: dict[str, Any]
    cost_usd: float
    tokens_in: int
    tokens_out: int
    duration_ms: int
    used_premium: bool
    bull: dict[str, Any] | None = None
    bear: dict[str, Any] | None = None
    risk_final: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


# --- Public entrypoint --------------------------------------------------------------------


async def synthesize_from_cache(
    symbol: str,
    *,
    factory: sessionmaker,
    embedder: Embedder,
    raw_bucket: str | None = None,
    user_id: str = "00000000-0000-0000-0000-000000000000",
    use_premium: bool = False,
    analysis_id: str | None = None,
    analysis_type: str = AnalysisType.DEEP_DIVE.value,
) -> SynthesisRunResult:
    """Synthesize a final analysis from cached specialist outputs.

    Raises ``MissingSpecialists`` if the cache is incomplete. Returns a
    SynthesisRunResult on both success and synthesis_invalid (the run still
    persists in either case so the operator has an audit trail).
    """
    sym = symbol.upper()
    started = time.perf_counter()
    aid = analysis_id or str(uuid.uuid4())

    # 1. Pull the full set of cache rows.
    state_priors, missing, stale = _load_priors_from_cache(factory, sym)
    if missing or stale:
        log.warning(
            "synthesize_blocked_missing_specialists",
            symbol=sym,
            missing=missing,
            stale=stale,
        )
        raise MissingSpecialists(missing=missing, stale=stale)

    # 2. Build the synthesis-ready state. The bull/bear/risk_final specialists
    # read `state.prior` (a dict of upstream outputs) rather than `state` directly,
    # so we hold both shapes here.
    base_state: dict[str, Any] = {
        "symbol": sym,
        "analysis_id": aid,
        "user_id": user_id,
        "context": {"use_premium_synthesis": use_premium},
        "cost_running_total": 0.0,
        "tokens_in_total": 0,
        "tokens_out_total": 0,
        "model_calls_total": 0,
    }
    base_state.update(state_priors)

    # 3. Run bull/bear/risk_final sequentially (the LangGraph mini-graph), then
    # synthesis. We use the same Sonnet-by-default pattern as the orchestrator;
    # premium=True flips synthesis only.
    extras: list[SpecialistResult] = []
    bull_state, bull_result = await _run_with_priors(
        BullResearcher(),
        sym,
        aid,
        user_id,
        factory,
        embedder,
        raw_bucket,
        priors_for_debate(state_priors),
        Model(model_id=MODEL_SONNET),
    )
    extras.append(bull_result)
    if bull_state is not None:
        base_state["bull"] = bull_state

    bear_state, bear_result = await _run_with_priors(
        BearResearcher(),
        sym,
        aid,
        user_id,
        factory,
        embedder,
        raw_bucket,
        priors_for_debate(state_priors),
        Model(model_id=MODEL_SONNET),
    )
    extras.append(bear_result)
    if bear_state is not None:
        base_state["bear"] = bear_state

    risk_priors = dict(priors_for_debate(state_priors))
    if base_state.get("bull") is not None:
        risk_priors["bull"] = base_state["bull"]
    if base_state.get("bear") is not None:
        risk_priors["bear"] = base_state["bear"]
    risk_state, risk_result = await _run_with_priors(
        RiskSpecialist(),
        sym,
        aid,
        user_id,
        factory,
        embedder,
        raw_bucket,
        risk_priors,
        Model(model_id=MODEL_SONNET),
    )
    extras.append(risk_result)
    if risk_state is not None:
        base_state["risk"] = risk_state

    # Roll bull/bear/risk-final cost into the running total so synthesis sees the
    # accurate pre-synthesis spend.
    for r in extras:
        base_state["cost_running_total"] = float(base_state.get("cost_running_total", 0.0)) + float(
            r.cost_usd
        )
        base_state["tokens_in_total"] = int(base_state.get("tokens_in_total", 0)) + int(r.tokens_in)
        base_state["tokens_out_total"] = int(base_state.get("tokens_out_total", 0)) + int(
            r.tokens_out
        )
        base_state["model_calls_total"] = int(base_state.get("model_calls_total", 0)) + int(
            r.model_calls
        )

    delta = await run_synthesis(base_state, use_premium=use_premium)
    final = delta.get("final") or {}
    synth_status = delta.get("synthesis_status", "ok")

    total_cost = float(base_state.get("cost_running_total", 0.0)) + float(
        delta.get("cost_running_total", 0.0)
    )
    total_in = int(base_state.get("tokens_in_total", 0)) + int(delta.get("tokens_in_total", 0))
    total_out = int(base_state.get("tokens_out_total", 0)) + int(delta.get("tokens_out_total", 0))
    total_calls = int(base_state.get("model_calls_total", 0)) + int(
        delta.get("model_calls_total", 0)
    )

    duration_ms = int((time.perf_counter() - started) * 1000)
    persist_status = (
        AnalysisStatus.SUCCEEDED.value
        if synth_status == "ok"
        else AnalysisStatus.FAILED.value
    )
    _persist_analysis(
        factory,
        analysis_id=aid,
        symbol=sym,
        final=final,
        cache_priors=state_priors,
        bull=base_state.get("bull"),
        bear=base_state.get("bear"),
        risk_final=base_state.get("risk"),
        total_cost_usd=total_cost,
        total_in=total_in,
        total_out=total_out,
        total_calls=total_calls,
        duration_ms=duration_ms,
        synth_status=synth_status,
        persist_status=persist_status,
        used_premium=use_premium,
        analysis_type=analysis_type,
    )

    log.info(
        "synthesize_persisted",
        symbol=sym,
        analysis_id=aid,
        synthesis_status=synth_status,
        cost_usd=round(total_cost, 6),
    )
    return SynthesisRunResult(
        analysis_id=aid,
        symbol=sym,
        status=synth_status,
        final=final,
        cost_usd=total_cost,
        tokens_in=total_in,
        tokens_out=total_out,
        duration_ms=duration_ms,
        used_premium=use_premium,
        bull=base_state.get("bull"),
        bear=base_state.get("bear"),
        risk_final=base_state.get("risk"),
    )


# --- Helpers -----------------------------------------------------------------------------


def _load_priors_from_cache(
    factory: sessionmaker, symbol: str
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Return (priors_in_state_shape, missing_canonical_names, stale_canonical_names)."""
    sym = symbol.upper()
    found: dict[str, SpecialistCache] = {}
    with session_scope(factory) as s:
        rows = (
            s.execute(select(SpecialistCache).where(SpecialistCache.symbol == sym))
            .scalars()
            .all()
        )
        for r in rows:
            found[r.specialist_name] = r

    state_priors: dict[str, Any] = {}
    missing: list[str] = []
    stale: list[str] = []
    for name in CACHEABLE_SPECIALISTS:
        row = found.get(name)
        if row is None:
            missing.append(name)
            continue
        if not is_fresh(name, row.last_run_at, status=row.status):
            stale.append(name)
            continue
        state_priors[_STATE_KEY_FROM_CACHE[name]] = row.output_json or {}
    return state_priors, missing, stale


def priors_for_debate(state_priors: dict[str, Any]) -> dict[str, Any]:
    """Bull/Bear/Risk-final read from `ctx.prior` which is a flat dict keyed on
    the AnalysisState keys (fundamentals, valuation, ..., news_sentiment,
    insider_flow). Same shape as `state_priors` minus the synthesis-only fields."""
    return {
        k: v
        for k, v in state_priors.items()
        if k
        in {
            "fundamentals",
            "valuation",
            "moat",
            "macro",
            "technical",
            "news_sentiment",
            "insider_flow",
            "risk_preliminary",
        }
    }


async def _run_with_priors(
    spec: Any,
    symbol: str,
    analysis_id: str,
    user_id: str,
    factory: sessionmaker,
    embedder: Embedder,
    raw_bucket: str | None,
    prior: dict[str, Any],
    model: Model,
) -> tuple[dict[str, Any] | None, SpecialistResult]:
    ctx = SpecialistContext(
        symbol=symbol,
        analysis_id=analysis_id,
        user_id=user_id,
        factory=factory,
        embedder=embedder,
        raw_bucket=raw_bucket,
        context={},
        prior=prior,
    )
    try:
        result = await spec.run(ctx, model)
    except Exception as exc:
        log.exception("synth_pipeline_specialist_failed", specialist=getattr(spec, "name", "?"))
        return None, SpecialistResult(output=None, error=str(exc)[:300], status="error")
    out = (
        result.output.model_dump(mode="json")
        if result.output is not None and hasattr(result.output, "model_dump")
        else None
    )
    return out, result


def _persist_analysis(
    factory: sessionmaker,
    *,
    analysis_id: str,
    symbol: str,
    final: dict[str, Any],
    cache_priors: dict[str, Any],
    bull: dict[str, Any] | None,
    bear: dict[str, Any] | None,
    risk_final: dict[str, Any] | None,
    total_cost_usd: float,
    total_in: int,
    total_out: int,
    total_calls: int,
    duration_ms: int,
    synth_status: str,
    persist_status: str,
    used_premium: bool,
    analysis_type: str = AnalysisType.DEEP_DIVE.value,
) -> None:
    now = datetime.now(UTC)
    rec = (final or {}).get("recommendation")
    conf = (final or {}).get("confidence")
    score = (final or {}).get("fii_score")
    thesis = (final or {}).get("thesis")
    model_calls_json = {
        "synthesis_status": synth_status,
        "synthesis_model": MODEL_OPUS if used_premium else MODEL_SONNET,
        "duration_ms": duration_ms,
    }
    with session_scope(factory) as s:
        stmt = pg_insert(Analysis).values(
            analysis_id=analysis_id,
            symbol=symbol,
            analysis_type=analysis_type,
            status=persist_status,
            initiated_at=now,
            completed_at=now,
            recommendation=rec,
            confidence=conf,
            fii_score=Decimal(str(round(float(score), 2))) if score is not None else None,
            orchestrator_summary=thesis,
            total_cost_usd=Decimal(str(round(total_cost_usd, 4))),
            total_tokens_in=int(total_in),
            total_tokens_out=int(total_out),
            model_calls_json={**model_calls_json, "model_calls_total": int(total_calls)},
            prompt_versions_json={},
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Analysis.analysis_id],
            set_={
                "status": stmt.excluded.status,
                "completed_at": stmt.excluded.completed_at,
                "recommendation": stmt.excluded.recommendation,
                "confidence": stmt.excluded.confidence,
                "fii_score": stmt.excluded.fii_score,
                "orchestrator_summary": stmt.excluded.orchestrator_summary,
                "total_cost_usd": stmt.excluded.total_cost_usd,
                "total_tokens_in": stmt.excluded.total_tokens_in,
                "total_tokens_out": stmt.excluded.total_tokens_out,
                "model_calls_json": stmt.excluded.model_calls_json,
            },
        )
        s.execute(stmt)

        # Per-specialist rows for the cacheable 8 (read straight off cache_priors,
        # which we know is fresh by the time we get here).
        for cache_name, enum in _SPECIALIST_ENUM.items():
            state_key = _STATE_KEY_FROM_CACHE[cache_name]
            obj = cache_priors.get(state_key)
            if obj is None:
                continue
            row_stmt = pg_insert(AnalysisSpecialistOutput).values(
                analysis_id=analysis_id,
                specialist_name=enum.value,
                output_json=obj,
                reasoning_text=obj.get("qualitative_summary") if isinstance(obj, dict) else None,
                citations_json={},
                model_used=None,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0,
                duration_ms=0,
            )
            row_stmt = row_stmt.on_conflict_do_update(
                constraint="uq_analysis_specialist",
                set_={
                    "output_json": row_stmt.excluded.output_json,
                    "reasoning_text": row_stmt.excluded.reasoning_text,
                },
            )
            s.execute(row_stmt)

        # Bull / Bear / Risk-final rows live only on the analyses (not the
        # specialist cache) since they're a function of the priors.
        for enum, obj in (
            (SpecialistName.BULL, bull),
            (SpecialistName.BEAR, bear),
        ):
            if obj is None:
                continue
            row_stmt = pg_insert(AnalysisSpecialistOutput).values(
                analysis_id=analysis_id,
                specialist_name=enum.value,
                output_json=obj,
                reasoning_text=obj.get("qualitative_summary") if isinstance(obj, dict) else None,
                citations_json={},
                model_used=MODEL_SONNET,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0,
                duration_ms=0,
            )
            row_stmt = row_stmt.on_conflict_do_update(
                constraint="uq_analysis_specialist",
                set_={
                    "output_json": row_stmt.excluded.output_json,
                    "reasoning_text": row_stmt.excluded.reasoning_text,
                },
            )
            s.execute(row_stmt)
        # Risk-final overwrites the risk row on the analysis; cache_priors carries
        # the preliminary risk pre-debate but the final pass is what synthesis used.
        if risk_final is not None:
            row_stmt = pg_insert(AnalysisSpecialistOutput).values(
                analysis_id=analysis_id,
                specialist_name=SpecialistName.RISK.value,
                output_json=risk_final,
                reasoning_text=(
                    risk_final.get("qualitative_summary")
                    if isinstance(risk_final, dict)
                    else None
                ),
                citations_json={},
                model_used=MODEL_SONNET,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0,
                duration_ms=0,
            )
            row_stmt = row_stmt.on_conflict_do_update(
                constraint="uq_analysis_specialist",
                set_={
                    "output_json": row_stmt.excluded.output_json,
                    "reasoning_text": row_stmt.excluded.reasoning_text,
                },
            )
            s.execute(row_stmt)
