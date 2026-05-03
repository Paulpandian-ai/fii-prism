"""Orchestrator graph assembly.

DAG:
  START
   └─> load_context
        ├─> fundamentals
        ├─> valuation
        ├─> moat
        ├─> macro
        ├─> technical
        ├─> news_sentiment
        ├─> insider_flow
        └─> risk_preliminary
              ↓ (join on all 8)
         bull ── bear (both run after the join)
              ↓ (join)
         risk_final
              ↓
         synthesis
              ↓
         persist
              ↓
           END

Checkpointing uses langgraph-checkpoint-postgres. Thread ID = analysis_id so we can
resume a run from the last completed node.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fii_data_clients import Embedder
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import sessionmaker

from fii_agents.budget import Budget
from fii_agents.nodes import make_nodes
from fii_agents.state import AnalysisState

log = structlog.get_logger(__name__)


PRELIMINARY = (
    "fundamentals",
    "valuation",
    "moat",
    "macro",
    "technical",
    "news_sentiment",
    "insider_flow",
    "risk_preliminary",
)
RESEARCHERS = ("bull", "bear")


def build_graph(
    factory: sessionmaker,
    *,
    embedder: Embedder | None = None,
    raw_bucket: str | None = None,
    budget: Budget | None = None,
) -> StateGraph:
    nodes = make_nodes(factory, embedder=embedder, raw_bucket=raw_bucket, budget=budget)

    g = StateGraph(AnalysisState)

    g.add_node("load_context", nodes["load_context"])
    for name in PRELIMINARY:
        g.add_node(name, nodes[name])
    for name in RESEARCHERS:
        g.add_node(name, nodes[name])
    g.add_node("risk_final", nodes["risk_final"])
    g.add_node("synthesis", nodes["synthesis"])
    g.add_node("persist", nodes["persist"])

    g.add_edge(START, "load_context")

    # Fan out from load_context to the 8 preliminary specialists.
    for name in PRELIMINARY:
        g.add_edge("load_context", name)

    # Join: bull and bear each depend on all 8 preliminaries.
    for researcher in RESEARCHERS:
        for prelim in PRELIMINARY:
            g.add_edge(prelim, researcher)

    # risk_final joins after bull + bear.
    g.add_edge("bull", "risk_final")
    g.add_edge("bear", "risk_final")

    # Linear tail.
    g.add_edge("risk_final", "synthesis")
    g.add_edge("synthesis", "persist")
    g.add_edge("persist", END)

    return g


# --- Checkpointer helpers ----------------------------------------------------------------


def _psycopg_url(sqlalchemy_url: str) -> str:
    """LangGraph's postgres saver wants the raw psycopg DSN, not SQLAlchemy's prefix."""
    url = sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


@asynccontextmanager
async def checkpointer_from_url(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    dsn = _psycopg_url(database_url)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        yield saver


async def ensure_checkpoint_tables(database_url: str) -> None:
    """Idempotent setup — creates the langgraph_* tables if they don't exist."""
    async with checkpointer_from_url(database_url) as saver:
        await saver.setup()
    log.info("checkpoint_tables_ready")


# --- Run entry points --------------------------------------------------------------------


async def run_analysis(
    *,
    symbol: str,
    analysis_id: str,
    user_id: str,
    database_url: str,
    factory: sessionmaker,
    embedder: Embedder | None = None,
    raw_bucket: str | None = None,
    budget: Budget | None = None,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one analysis end-to-end (blocking). Used by the API's background task
    path and the advisor's `run_quick` tool.

    Routes through the new per-specialist + cached pipeline rather than the
    LangGraph fan-out: each cacheable specialist is run via `run_specialist`
    (cache-aware — fresh rows are reused), then `synthesize_from_cache` produces
    the final. For per-node SSE updates, prefer `stream_analysis` below which
    still uses LangGraph.

    `extra_context` is forwarded onto each specialist's context dict and may
    carry {"use_premium_synthesis": True, "model_tier": "haiku"}.
    """
    from fii_data_clients import make_embedder
    from fii_db import AnalysisType

    from fii_agents.cache_policy import CACHEABLE_SPECIALISTS
    from fii_agents.specialist_runner import run_specialist
    from fii_agents.synthesis_runner import MissingSpecialists, synthesize_from_cache

    embedder = embedder or make_embedder()
    extras = dict(extra_context or {})
    use_premium = bool(extras.get("use_premium_synthesis", False))
    model_tier = "haiku" if extras.get("model_tier") == "haiku" else "sonnet"
    quick_event = extras.get("quick_refresh_event_type")
    analysis_type = (
        AnalysisType.QUICK_REFRESH.value if quick_event else AnalysisType.DEEP_DIVE.value
    )

    # In quick-refresh mode, only the event-scoped specialists need a forced re-run;
    # off-scope specialists fall back to whatever's already in cache (the deep-dive
    # baseline). In deep-dive mode, all 8 are run with cache-aware no-force.
    scope_canonical = _quick_refresh_scope(quick_event) if quick_event else None
    for name in CACHEABLE_SPECIALISTS:
        force_this = scope_canonical is not None and name in scope_canonical
        if scope_canonical is not None and not force_this:
            # Off-scope in quick-refresh: skip if missing — synthesize_from_cache
            # will surface the gap. Otherwise reuse the cache row.
            continue
        await run_specialist(
            name,
            symbol=symbol,
            factory=factory,
            embedder=embedder,
            raw_bucket=raw_bucket,
            user_id=user_id,
            analysis_id=analysis_id,
            context=extras,
            force=force_this if scope_canonical is not None else False,
            model_tier=model_tier,
        )

    try:
        synth = await synthesize_from_cache(
            symbol,
            factory=factory,
            embedder=embedder,
            raw_bucket=raw_bucket,
            user_id=user_id,
            use_premium=use_premium,
            analysis_id=analysis_id,
            analysis_type=analysis_type,
        )
    except MissingSpecialists as exc:
        log.warning(
            "run_analysis_blocked_specialists_not_ready",
            analysis_id=analysis_id,
            symbol=symbol,
            missing=exc.missing,
            stale=exc.stale,
        )
        return {
            "symbol": symbol,
            "analysis_id": analysis_id,
            "status": "blocked",
            "missing": exc.missing,
            "stale": exc.stale,
        }

    # Re-hydrate the per-specialist outputs into the return dict so callers (and
    # legacy tests) can still read state.get("fundamentals"), etc., the way the
    # LangGraph fan-out used to populate them.
    from fii_db import SpecialistCache
    from fii_db.session import session_scope
    from sqlalchemy import select as _select

    from fii_agents.synthesis_runner import _STATE_KEY_FROM_CACHE

    legacy_state: dict[str, Any] = {}
    with session_scope(factory) as s:
        rows = (
            s.execute(_select(SpecialistCache).where(SpecialistCache.symbol == symbol.upper()))
            .scalars()
            .all()
        )
        for r in rows:
            key = _STATE_KEY_FROM_CACHE.get(r.specialist_name)
            if key:
                legacy_state[key] = r.output_json or {}
    if synth.bull is not None:
        legacy_state["bull"] = synth.bull
    if synth.bear is not None:
        legacy_state["bear"] = synth.bear
    if synth.risk_final is not None:
        legacy_state["risk"] = synth.risk_final

    return {
        "symbol": synth.symbol,
        "analysis_id": synth.analysis_id,
        "status": synth.status,
        "final": synth.final,
        "cost_running_total": synth.cost_usd,
        "tokens_in_total": synth.tokens_in,
        "tokens_out_total": synth.tokens_out,
        "timings_ms": {"total": synth.duration_ms},
        **legacy_state,
    }


async def stream_analysis(
    *,
    symbol: str,
    analysis_id: str,
    user_id: str,
    database_url: str,
    factory: sessionmaker,
    embedder: Embedder | None = None,
    raw_bucket: str | None = None,
    budget: Budget | None = None,
    extra_context: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream LangGraph events for one analysis. Each event is a dict ready to be
    serialized and pushed over SSE.
    """
    graph = build_graph(factory, embedder=embedder, raw_bucket=raw_bucket, budget=budget)
    async with checkpointer_from_url(database_url) as saver:
        app = graph.compile(checkpointer=saver)
        initial: AnalysisState = {
            "symbol": symbol,
            "analysis_id": analysis_id,
            "user_id": user_id,
            "context": dict(extra_context or {}),
        }
        config = {"configurable": {"thread_id": analysis_id}}
        # `updates` stream_mode yields one dict per node completion — ideal for SSE.
        async for event in app.astream(initial, config, stream_mode="updates"):
            for node_name, _ in event.items():
                yield {"node": node_name, "status": "complete"}


# Event-type → canonical-cache-name scope. Mirrors the LangGraph node-name scope
# in `nodes.py:_QUICK_REFRESH_SCOPE` but uses the short cache identifiers (news /
# insider / risk) rather than the long node names. `risk` is always in scope so
# every quick-refresh re-evaluates risk.
_QUICK_REFRESH_SCOPE_CANONICAL: dict[str, set[str]] = {
    "price_shock": {"technical", "news", "risk"},
    "news_shock": {"news", "risk"},
    "8k_filed": {"fundamentals", "news", "risk"},
    "earnings_release": {"fundamentals", "valuation", "news", "risk"},
    "macro_surprise": {"macro", "risk"},
}


def _quick_refresh_scope(event_type: str | None) -> set[str] | None:
    if not event_type:
        return None
    return _QUICK_REFRESH_SCOPE_CANONICAL.get(event_type)


def disable_fake_when_key_present() -> None:
    """Call at startup to make sure real calls happen when the key exists."""
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("FII_USE_FAKE_MODEL") != "1":
        os.environ.pop("FII_USE_FAKE_MODEL", None)
