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
) -> dict[str, Any]:
    """Run one analysis to completion (blocking). Used by tests and the API's
    background task path. For live UX, prefer stream_analysis() below.
    """
    graph = build_graph(factory, embedder=embedder, raw_bucket=raw_bucket, budget=budget)
    async with checkpointer_from_url(database_url) as saver:
        app = graph.compile(checkpointer=saver)
        initial: AnalysisState = {
            "symbol": symbol,
            "analysis_id": analysis_id,
            "user_id": user_id,
        }
        final_state = await app.ainvoke(initial, {"configurable": {"thread_id": analysis_id}})
    return final_state


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
        }
        config = {"configurable": {"thread_id": analysis_id}}
        # `updates` stream_mode yields one dict per node completion — ideal for SSE.
        async for event in app.astream(initial, config, stream_mode="updates"):
            # event is {node_name: partial_state}; emit one message per node.
            for node_name, _ in event.items():
                yield {"node": node_name, "status": "complete"}


def disable_fake_when_key_present() -> None:
    """Call at startup to make sure real calls happen when the key exists."""
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("FII_USE_FAKE_MODEL") != "1":
        os.environ.pop("FII_USE_FAKE_MODEL", None)
