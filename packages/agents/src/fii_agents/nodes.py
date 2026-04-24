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
from fii_shared import CitedClaim, CostSummary, OrchestratorFinalOutput, SourceRef, SourceType
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from fii_agents.budget import Budget
from fii_agents.model import MODEL_SONNET, Model
from fii_agents.specialists.base import Specialist, SpecialistContext
from fii_agents.specialists.fundamentals import FundamentalsSpecialist
from fii_agents.specialists.stubs import (
    BearStub,
    BullStub,
    InsiderFlowStub,
    MacroStub,
    MoatStub,
    NewsSentimentStub,
    RiskStub,
    TechnicalStub,
    ValuationStub,
)
from fii_agents.state import AnalysisState, SpecialistError

log = structlog.get_logger(__name__)


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

    # Specialists we run in the fan-out.
    preliminary_specialists: dict[str, Specialist] = {
        "fundamentals": FundamentalsSpecialist(),
        "valuation": ValuationStub(),
        "moat": MoatStub(),
        "macro": MacroStub(),
        "technical": TechnicalStub(),
        "news_sentiment": NewsSentimentStub(),
        "insider_flow": InsiderFlowStub(),
        "risk_preliminary": RiskStub(),
    }
    researchers: dict[str, Specialist] = {"bull": BullStub(), "bear": BearStub()}
    risk_final: Specialist = RiskStub()

    async def _load_context(state: AnalysisState) -> dict[str, Any]:
        log.info("node_load_context_start", symbol=state["symbol"])
        started_at = time.perf_counter()
        context: dict[str, Any] = {"as_of": datetime.now(UTC).isoformat()}
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
            model = _model_for(name)
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
                delta[_state_key_for(name)] = result.output
            if result.error:
                delta["errors"] = [
                    {"specialist": name, "kind": "tool_error", "message": result.error}
                ]
            return delta

        return _node

    async def _synthesis(state: AnalysisState) -> dict[str, Any]:
        """Deterministic synthesis for Section 4. Real Opus/Sonnet synthesis lands in
        Section 5. We build an OrchestratorFinalOutput from the specialist outputs.
        """
        started = time.perf_counter()
        fii_score = _score(state)
        recommendation = _recommendation_from_score(fii_score)
        risk = state.get("risk") or state.get("risk_preliminary")
        stress_outcomes = dict(risk.dfast_scenarios) if risk else {}

        src = SourceRef(
            source_type=SourceType.CALCULATED,
            source_id="synthesis/section4",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        wrong_claims = [
            CitedClaim(
                claim="The fundamentals signal could decay if services growth slows.",
                sources=[src],
                confidence="medium",
            ),
            CitedClaim(
                claim="Regulatory outcomes could compress platform economics.",
                sources=[src],
                confidence="medium",
            ),
            CitedClaim(
                claim="A multiple de-rating could wipe out near-term upside.",
                sources=[src],
                confidence="low",
            ),
        ]

        summaries: dict[str, str] = {}
        for name in (
            "fundamentals",
            "valuation",
            "moat",
            "macro",
            "technical",
            "news_sentiment",
            "insider_flow",
        ):
            obj = state.get(name)
            if obj is not None and hasattr(obj, "qualitative_summary"):
                summaries[name] = _first_sentence(obj.qualitative_summary)

        what_i_would_buy: str | None = None
        if recommendation in ("buy", "strong_buy"):
            tech = state.get("technical")
            risk_obj = state.get("risk") or state.get("risk_preliminary")
            entry = tech.suggested_entry.value if tech else 0.0
            stop = tech.suggested_stop.value if tech else 0.0
            size = risk_obj.position_size_rec_pct if risk_obj else 0.0
            what_i_would_buy = (
                f"Entry near [${entry:.2f}:technical.suggested_entry], "
                f"hard stop below [${stop:.2f}:technical.suggested_stop], "
                f"size at [{size:.1f}%:risk.position_size_rec_pct] of portfolio. "
                "(Section-4 stub — refine in Section 5.)"
            )

        final = OrchestratorFinalOutput(
            symbol=state["symbol"].upper(),
            analysis_id=state["analysis_id"],
            recommendation=recommendation,
            confidence="medium",
            fii_score=float(fii_score),
            thesis=(
                f"{state['symbol'].upper()} synthesis based on specialist outputs. "
                "This is a Section-4 walking-skeleton synthesis; real Opus/Sonnet "
                "reasoning lands in Section 5. Fundamentals scoring is real when an "
                "Anthropic key is present; other specialists are stubs."
            ),
            what_i_would_buy=what_i_would_buy,
            what_could_make_me_wrong=wrong_claims,
            time_horizon="long (years)",
            specialist_summaries=summaries,
            stress_outcomes=stress_outcomes,
            cost_summary=CostSummary(
                total_usd=float(state.get("cost_running_total", 0.0)),
                input_tokens=int(state.get("tokens_in_total", 0)),
                output_tokens=int(state.get("tokens_out_total", 0)),
                model_calls=int(state.get("model_calls_total", 0)),
            ),
        )

        return {
            "final": final,
            "timings_ms": {"synthesis": int((time.perf_counter() - started) * 1000)},
        }

    async def _persist(state: AnalysisState) -> dict[str, Any]:
        """Write the Analysis row + one AnalysisSpecialistOutput per specialist."""
        started = time.perf_counter()
        final: OrchestratorFinalOutput | None = state.get("final")

        with session_scope(factory) as s:
            analysis_stmt = pg_insert(Analysis).values(
                analysis_id=state["analysis_id"],
                symbol=state["symbol"].upper(),
                analysis_type=AnalysisType.DEEP_DIVE.value,
                status=AnalysisStatus.SUCCEEDED.value,
                initiated_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                orchestrator_summary=(final.thesis if final else None),
                recommendation=(final.recommendation if final else None),
                confidence=("medium" if final else None),
                fii_score=(float(final.fii_score) if final else None),
                total_cost_usd=float(state.get("cost_running_total", 0.0)),
                total_tokens_in=int(state.get("tokens_in_total", 0)),
                total_tokens_out=int(state.get("tokens_out_total", 0)),
                model_calls_json={"per_specialist": state.get("timings_ms", {})},
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
                row_stmt = pg_insert(AnalysisSpecialistOutput).values(
                    analysis_id=state["analysis_id"],
                    specialist_name=enum.value,
                    output_json=obj.model_dump(mode="json"),
                    reasoning_text=getattr(obj, "qualitative_summary", None),
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


def _model_for(specialist_name: str) -> Model:
    # Only Fundamentals uses a real model in Section 4. Others short-circuit via fake mode.
    if specialist_name == "fundamentals":
        return Model(model_id=MODEL_SONNET)
    # Stubs don't call respond(); a fake-mode Model is harmless placeholder.
    from os import environ

    environ.setdefault("FII_USE_FAKE_MODEL", "1") if False else None  # no-op
    return Model(model_id=MODEL_SONNET)


def _model_id_for(name: str) -> str:
    return MODEL_SONNET if name == "fundamentals" else "stub"


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
