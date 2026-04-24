"""Per-process agents runtime.

One engine / session factory / embedder shared by all request handlers. Initialized
at FastAPI startup (via lifespan), torn down on shutdown. The LangGraph checkpointer
opens its own short-lived connection inside run_analysis/stream_analysis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import structlog
from fii_agents import Budget, ensure_checkpoint_tables, seed_defaults
from fii_data_clients import Embedder, make_embedder
from fii_db import get_engine, get_session_factory
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

log = structlog.get_logger(__name__)


@dataclass
class AgentsRuntime:
    engine: Engine
    session_factory: sessionmaker
    embedder: Embedder
    database_url: str
    raw_bucket: str | None
    budget: Budget


_SINGLETON: AgentsRuntime | None = None


async def build_runtime() -> AgentsRuntime:
    """Initialize the runtime. Safe to call once at startup."""
    settings = get_settings()
    database_url = settings.database_url or ""

    engine = get_engine(database_url)
    factory = get_session_factory(engine)

    # Embeddings: Voyage if VOYAGE_API_KEY, else Bedrock (which needs AWS creds). In
    # local dev without either, the specialist tools that use embeddings will surface
    # clear errors; the graph still completes with stub specialists.
    try:
        embedder = make_embedder()
    except Exception as exc:
        log.warning("embedder_init_failed_using_shim", error=str(exc))
        embedder = _ShimEmbedder()

    raw_bucket = os.environ.get("RAW_DATA_BUCKET")
    budget = Budget.from_env()

    # Checkpointer tables + prompt seeding. Both idempotent.
    try:
        await ensure_checkpoint_tables(database_url)
    except Exception:
        log.exception("checkpoint_setup_failed — analyses will fail until resolved")
    try:
        seed_defaults(factory)
    except Exception:
        log.exception("prompt_seed_failed — loader will fall back to bundled defaults")

    runtime = AgentsRuntime(
        engine=engine,
        session_factory=factory,
        embedder=embedder,
        database_url=database_url,
        raw_bucket=raw_bucket,
        budget=budget,
    )
    global _SINGLETON
    _SINGLETON = runtime
    log.info("agents_runtime_ready", raw_bucket=raw_bucket, budget_usd=budget.hard_cap_usd)
    return runtime


def get_runtime() -> AgentsRuntime:
    """FastAPI dependency. Fails clearly if startup hasn't run."""
    if _SINGLETON is None:
        raise RuntimeError("AgentsRuntime not initialized; startup did not run.")
    return _SINGLETON


class _ShimEmbedder:
    """No-op embedder so RAG tools don't crash when Voyage/Bedrock creds are absent.

    Returns a zero-vector of the correct dimension. Callers can still query pgvector;
    results will be semantically meaningless but the plumbing works.
    """

    model = "shim"

    async def embed(self, texts, *, input_type: str = "document"):
        from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult

        return EmbeddingResult(
            vectors=[[0.0] * EMBEDDING_DIM for _ in texts],
            model=self.model,
        )
