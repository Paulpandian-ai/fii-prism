"""LangGraph nodes. Each node is a coroutine that takes AnalysisState and returns a
partial state dict to merge via the reducers declared on the state TypedDict.

Every specialist node is wrapped with `_run_specialist` which handles:
  - Budget check (skip if running total >= hard cap)
  - Try/except → records SpecialistError in state.errors
  - Schema validation (specialists return Pydantic models; we dump to dict for the graph)
  - Timing + token + cost accumulation
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import structlog
from fii_data_clients import Embedder, make_embedder
from fii_db import (
    Analysis,
    AnalysisSpecialistOutput,
    AnalysisStatus,
    AnalysisType,
    SpecialistName,
)
from fii_db.session import session_scope
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from fii_agents.budget import Budget
from fii_agents.model import MODEL_HAIKU, MODEL_SONNET, Model
from fii_agents.specialists.base import Specialist, SpecialistContext
from fii_agents.specialists.debate import BearResearcher, BullResearcher
from fii_agents.specialists.fundamentals import FundamentalsSpecialist
from fii_agents.specialists.insider import InsiderFlowSpecialist
from fii_agents.specialists.macro import MacroSpecialist
from fii_agents.specialists.moat import MoatSpecialist
from fii_agents.specialists.news import NewsSentimentSpecialist
from fii_agents.specialists.risk import RiskSpecialist
from fii_agents.specialists.synthesis import run_synthesis
from fii_agents.specialists.technical import TechnicalSpecialist
from fii_agents.specialists.valuation import ValuationSpecialist
from fii_agents.state import AnalysisState, SpecialistError

log = structlog.get_logger(__name__)


# Event-scoped specialist subsets for quick_refresh. Keys match the state keys we skip
# (same as the LangGraph node names for preliminaries). Any specialist NOT in the set
# for a given event_type is skipped (returns no output) during a quick_refresh run.
# Bull/bear/risk_final/synthesis/persist always run so the analysis still produces an
# OrchestratorFinalOutput.
_PRELIMINARY_NAMES: set[str] = {
    "fundamentals",
    "valuation",
    "moat",
    "macro",
    "technical",
    "news_sentiment",
    "insider_flow",
    "risk_preliminary",
}

_EVENT_SCOPES: dict[str, set[str]] = {
    "price_shock": {"technical", "news_sentiment", "risk_preliminary"},
    "news_shock": {"news_sentiment", "risk_preliminary"},
    "8k_filed": {"fundamentals", "news_sentiment", "risk_preliminary"},
    "earnings_release": {
        "fundamentals",
        "valuation",
        "news_sentiment",
        "risk_preliminary",
    },
    "macro_surprise": {"macro", "risk_preliminary"},
}


def _is_in_quick_refresh_scope(state: AnalysisState, preliminary_name: str) -> bool:
    """True when `preliminary_name` should run under the current quick_refresh event.

    Deep-dive runs (no quick_refresh_event_type) always return True.
    """
    event_type = (state.get("context") or {}).get("quick_refresh_event_type")
    if not event_type:
        return True
    scope = _EVENT_SCOPES.get(str(event_type))
    if scope is None:
        return True  # unknown event type → be permissive, not destructive
    return preliminary_name in scope


# --- Node factory ------------------------------------------------------------------------


def make_nodes(
    factory: sessionmaker,
    *,
    embedder: Embedder | None = None,
    raw_bucket: str | None = None,
    budget: Budget | None = None,
):
    """Build node callables bound to DB + provider dependencies."""
    embedder = embedder or make_embedder()
    budget = budget or Budget.from_env()

    # Specialists we run in the fan-out — all real implementations now (with fake-mode
    # fallbacks inside each, used when ANTHROPIC_API_KEY is unset).
    preliminary_specialists: dict[str, Specialist] = {
        "fundamentals": FundamentalsSpecialist(),
        "valuation": ValuationSpecialist(),
        "moat": MoatSpecialist(),
        "macro": MacroSpecialist(),
        "technical": TechnicalSpecialist(),
        "news_sentiment": NewsSentimentSpecialist(),
        "insider_flow": InsiderFlowSpecialist(),
        "risk_preliminary": RiskSpecialist(),
    }
    researchers: dict[str, Specialist] = {
        "bull": BullResearcher(),
        "bear": BearResearcher(),
    }
    risk_final: Specialist = RiskSpecialist()

    async def _load_context(state: AnalysisState) -> dict[str, Any]:
        log.info("node_load_context_start", symbol=state["symbol"])
        started_at = time.perf_counter()
        # Carry forward anything the API supplied as extra_context (e.g. use_premium_synthesis).
        context: dict[str, Any] = dict(state.get("context") or {})
        context["as_of"] = datetime.now(UTC).isoformat()
        try:
            from fii_db import Ticker

            with session_scope(factory) as s:
                # Minimal upsert so the analyses FK is always satisfied even if
                # the ingest pipeline hasn't run yet for this symbol.
                s.execute(
                    pg_insert(Ticker)
                    .values(symbol=state["symbol"].upper(), name=state["symbol"].upper())
                    .on_conflict_do_nothing(index_elements=[Ticker.symbol])
                )
                row = s.execute(
                    select(Ticker).where(Ticker.symbol == state["symbol"].upper())
                ).scalar_one_or_none()
                if row is not None:
                    context["ticker"] = {
                        "symbol": row.symbol,
                        "name": row.name,
                        "sector": row.sector,
                        "industry": row.industry,
                        "market_cap_bucket": row.market_cap_bucket,
                        "cik": row.cik,
                    }
        except Exception as exc:
            log.exception("load_context_failed", symbol=state["symbol"])
            context["error"] = str(exc)

        return {
            "context": context,
            "timings_ms": {"load_context": int((time.perf_counter() - started_at) * 1000)},
        }

    def _spec_node(name: str, spec: Specialist, *, prior_fields: list[str] | None = None):
        async def _node(state: AnalysisState) -> dict[str, Any]:
            # Quick-refresh event scoping: skip preliminary specialists that aren't in
            # the event-type's scope. Bull/bear/risk_final/synthesis/persist always run
            # so we still produce a final.
            if name in _PRELIMINARY_NAMES and not _is_in_quick_refresh_scope(state, name):
                return {"timings_ms": {name: 0}}

            if budget.is_exceeded(float(state.get("cost_running_total", 0.0))):
                err: SpecialistError = {
                    "specialist": name,
                    "kind": "budget_exceeded",
                    "message": f"Skipped: running total exceeds hard cap ${budget.hard_cap_usd:.2f}",
                }
                return {"errors": [err], "timings_ms": {name: 0}}

            ctx = SpecialistContext(
                symbol=state["symbol"],
                analysis_id=state["analysis_id"],
                user_id=state.get("user_id", "unknown"),
                factory=factory,
                embedder=embedder,
                raw_bucket=raw_bucket,
                context=state.get("context") or {},
                prior={k: state.get(k) for k in (prior_fields or []) if state.get(k) is not None},
            )
            model = _model_for(name, state)
            started = time.perf_counter()
            try:
                result = await spec.run(ctx, model)
            except Exception as exc:
                log.exception("specialist_exception", specialist=name)
                err = {"specialist": name, "kind": "exception", "message": str(exc)}
                return {
                    "errors": [err],
                    "timings_ms": {name: int((time.perf_counter() - started) * 1000)},
                }

            delta: dict[str, Any] = {
                "cost_running_total": float(result.cost_usd),
                "tokens_in_total": int(result.tokens_in),
                "tokens_out_total": int(result.tokens_out),
                "model_calls_total": int(result.model_calls),
                "timings_ms": {name: result.duration_ms},
            }
            if result.output is not None:
                # LangGraph's checkpoint serializer round-trips through ormsgpack which
                # doesn't natively encode `date` / `Decimal`. mode="json" yields JSON
                # primitives. Downstream consumers (synthesis, debate, persist) read
                # plain dicts.
                delta[_state_key_for(name)] = result.output.model_dump(mode="json")
            if result.error:
                delta["errors"] = [
                    {"specialist": name, "kind": "tool_error", "message": result.error}
                ]
            return delta

        return _node

    async def _synthesis(state: AnalysisState) -> dict[str, Any]:
        """LLM-backed synthesis (Sonnet 4.6 by default; Opus 4.7 when use_premium=True
        in state['context']). Falls back to deterministic synthesis when fake-mode is
        active or when the LLM call fails twice in a row."""
        use_premium = bool((state.get("context") or {}).get("use_premium_synthesis", False))
        return await run_synthesis(state, use_premium=use_premium)

    async def _persist(state: AnalysisState) -> dict[str, Any]:
        """Write the Analysis row + one AnalysisSpecialistOutput per specialist."""
        started = time.perf_counter()
        # `final` is a JSON dict by the time it reaches persist (see synthesis.py).
        final: dict | None = state.get("final")
        is_quick = bool((state.get("context") or {}).get("quick_refresh_event_type"))
        analysis_type_value = (
            AnalysisType.QUICK_REFRESH.value if is_quick else AnalysisType.DEEP_DIVE.value
        )

        # Capture which versioned prompts the specialists used. The journal pivots on these
        # so we can compare prompt v1 vs v2 performance.
        prompt_versions = _collect_prompt_versions(factory)

        with session_scope(factory) as s:
            analysis_stmt = pg_insert(Analysis).values(
                analysis_id=state["analysis_id"],
                symbol=state["symbol"].upper(),
                analysis_type=analysis_type_value,
                status=AnalysisStatus.SUCCEEDED.value,
                initiated_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                orchestrator_summary=(final.get("thesis") if final else None),
                recommendation=(final.get("recommendation") if final else None),
                confidence=(final.get("confidence") if final else None),
                fii_score=(
                    float(final["fii_score"])
                    if final and final.get("fii_score") is not None
                    else None
                ),
                total_cost_usd=float(state.get("cost_running_total", 0.0)),
                total_tokens_in=int(state.get("tokens_in_total", 0)),
                total_tokens_out=int(state.get("tokens_out_total", 0)),
                model_calls_json={"per_specialist": state.get("timings_ms", {})},
                prompt_versions_json=prompt_versions,
                step_functions_execution_arn=None,
                reasoning_trail_s3_key=None,
                user_notes=None,
            )
            analysis_stmt = analysis_stmt.on_conflict_do_update(
                index_elements=[Analysis.analysis_id],
                set_={
                    "status": AnalysisStatus.SUCCEEDED.value,
                    "completed_at": datetime.now(UTC),
                    "orchestrator_summary": analysis_stmt.excluded.orchestrator_summary,
                    "recommendation": analysis_stmt.excluded.recommendation,
                    "confidence": analysis_stmt.excluded.confidence,
                    "fii_score": analysis_stmt.excluded.fii_score,
                    "total_cost_usd": analysis_stmt.excluded.total_cost_usd,
                    "total_tokens_in": analysis_stmt.excluded.total_tokens_in,
                    "total_tokens_out": analysis_stmt.excluded.total_tokens_out,
                    "model_calls_json": analysis_stmt.excluded.model_calls_json,
                    "prompt_versions_json": analysis_stmt.excluded.prompt_versions_json,
                },
            )
            s.execute(analysis_stmt)

            # One row per specialist that ran successfully.
            for name, enum in (
                ("fundamentals", SpecialistName.FUNDAMENTALS),
                ("valuation", SpecialistName.VALUATION),
                ("moat", SpecialistName.MOAT),
                ("macro", SpecialistName.MACRO),
                ("technical", SpecialistName.TECHNICAL),
                ("news_sentiment", SpecialistName.NEWS),
                ("insider_flow", SpecialistName.INSIDER),
                ("risk", SpecialistName.RISK),
                ("bull", SpecialistName.BULL),
                ("bear", SpecialistName.BEAR),
            ):
                obj = state.get(name)
                if obj is None and name == "risk":
                    obj = state.get("risk_preliminary")
                if obj is None:
                    continue
                # State stores plain dicts now (see _spec_node) — no model_dump needed.
                row_stmt = pg_insert(AnalysisSpecialistOutput).values(
                    analysis_id=state["analysis_id"],
                    specialist_name=enum.value,
                    output_json=obj,
                    reasoning_text=obj.get("qualitative_summary")
                    if isinstance(obj, dict)
                    else None,
                    citations_json={},
                    model_used=_model_id_for(name),
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0,
                    duration_ms=int(state.get("timings_ms", {}).get(name, 0) or 0),
                )
                row_stmt = row_stmt.on_conflict_do_update(
                    constraint="uq_analysis_specialist",
                    set_={
                        "output_json": row_stmt.excluded.output_json,
                        "reasoning_text": row_stmt.excluded.reasoning_text,
                        "duration_ms": row_stmt.excluded.duration_ms,
                    },
                )
                s.execute(row_stmt)

        return {"timings_ms": {"persist": int((time.perf_counter() - started) * 1000)}}

    return {
        "load_context": _load_context,
        "fundamentals": _spec_node("fundamentals", preliminary_specialists["fundamentals"]),
        "valuation": _spec_node("valuation", preliminary_specialists["valuation"]),
        "moat": _spec_node("moat", preliminary_specialists["moat"]),
        "macro": _spec_node("macro", preliminary_specialists["macro"]),
        "technical": _spec_node("technical", preliminary_specialists["technical"]),
        "news_sentiment": _spec_node("news_sentiment", preliminary_specialists["news_sentiment"]),
        "insider_flow": _spec_node("insider_flow", preliminary_specialists["insider_flow"]),
        "risk_preliminary": _spec_node(
            "risk_preliminary", preliminary_specialists["risk_preliminary"]
        ),
        "bull": _spec_node(
            "bull",
            researchers["bull"],
            prior_fields=[
                "fundamentals",
                "valuation",
                "moat",
                "macro",
                "technical",
                "news_sentiment",
                "insider_flow",
            ],
        ),
        "bear": _spec_node(
            "bear",
            researchers["bear"],
            prior_fields=[
                "fundamentals",
                "valuation",
                "moat",
                "macro",
                "technical",
                "news_sentiment",
                "insider_flow",
            ],
        ),
        "risk_final": _spec_node(
            "risk",
            risk_final,
            prior_fields=[
                "fundamentals",
                "valuation",
                "moat",
                "macro",
                "technical",
                "news_sentiment",
                "insider_flow",
                "bull",
                "bear",
            ],
        ),
        "synthesis": _synthesis,
        "persist": _persist,
    }


# --- Helpers -----------------------------------------------------------------------------


def _state_key_for(name: str) -> str:
    return name  # identity mapping; keep names consistent with AnalysisState keys


def _model_for(specialist_name: str, state: AnalysisState | None = None) -> Model:
    """Pick the right model for this specialist.

    Quick-refresh runs override to Haiku 4.5 (cheap + fast) unless the specialist is
    on the Opus-only list (none today). Deep-dives use Sonnet 4.6.
    """
    tier = None
    if state is not None:
        tier = (state.get("context") or {}).get("model_tier")
    if tier == "haiku":
        return Model(model_id=MODEL_HAIKU)
    return Model(model_id=MODEL_SONNET)


def _model_id_for(name: str) -> str:
    return MODEL_SONNET if name == "fundamentals" else "stub"


def _collect_prompt_versions(factory: sessionmaker) -> dict[str, int]:
    """Snapshot the active prompt version for each specialist used in this run.

    Reads all active rows in one query so we don't burn ten sessions during persist.
    Falls back to {} on any error — the journal degrades gracefully when the table is
    empty.
    """
    from fii_db import AgentPrompt

    versions: dict[str, int] = {}
    try:
        with session_scope(factory) as s:
            rows = s.execute(
                select(AgentPrompt.specialist_name, AgentPrompt.version).where(
                    AgentPrompt.is_active.is_(True)
                )
            ).all()
        for name, version in rows:
            # Keep the highest active version per specialist (defensive against stale flags).
            existing = versions.get(name)
            v = int(version)
            if existing is None or v > existing:
                versions[name] = v
    except Exception:
        return {}
    return versions


def _first_sentence(text: str | None) -> str:
    if not text:
        return ""
    for sep in (". ", "! ", "? "):
        if sep in text:
            return text.split(sep, 1)[0] + sep.strip()
    return text[:200]


def _score(state: AnalysisState) -> float:
    """Very simple deterministic score: 5 baseline, +1 for each specialist that ran."""
    base = 5.0
    bumps = 0
    for name in (
        "fundamentals",
        "valuation",
        "moat",
        "macro",
        "technical",
        "news_sentiment",
        "insider_flow",
        "bull",
        "bear",
        "risk",
    ):
        if state.get(name) is not None:
            bumps += 1
    return min(10.0, base + 0.5 * bumps)


def _recommendation_from_score(score: float) -> str:
    if score >= 8.5:
        return "strong_buy"
    if score >= 7.0:
        return "buy"
    if score >= 5.0:
        return "hold"
    if score >= 3.5:
        return "trim"
    return "sell"
