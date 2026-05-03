"""Section 7: quick_refresh mode force-reruns the event-scoped specialists,
reuses the cached deep-dive output for off-scope ones, still produces a valid
final, and is persisted as QUICK_REFRESH.

Under the per-specialist cache design (Section 11), off-scope specialists are
NOT skipped from the response — their cached output is reused. We verify that
scoped specialists' cache rows were force-refreshed (newer last_run_at) while
off-scope rows kept their prior timestamp.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fii_agents import ensure_checkpoint_tables, run_analysis
from fii_agents.specialist_runner import run_specialist
from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult
from fii_db import Analysis, AnalysisType, SpecialistCache
from fii_db.session import session_scope
from sqlalchemy import select


class _Embedder:
    model = "shim"

    async def embed(self, texts, *, input_type: str = "document"):
        return EmbeddingResult(vectors=[[0.0] * EMBEDDING_DIM for _ in texts], model=self.model)


async def _seed_full_cache(session_factory, symbol: str) -> dict[str, datetime]:
    """Run every cacheable specialist once so all 8 cache rows exist. Returns the
    last_run_at per canonical name so the test can detect which were re-run."""
    names = ("fundamentals", "valuation", "moat", "macro", "technical", "news", "insider", "risk")
    timestamps: dict[str, datetime] = {}
    for n in names:
        result = await run_specialist(
            n,
            symbol=symbol,
            factory=session_factory,
            embedder=_Embedder(),
            force=True,
        )
        timestamps[n] = result.last_run_at
    return timestamps


def _read_cache_timestamps(session_factory, symbol: str) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    with session_scope(session_factory) as s:
        rows = (
            s.execute(select(SpecialistCache).where(SpecialistCache.symbol == symbol))
            .scalars()
            .all()
        )
        for r in rows:
            out[r.specialist_name] = r.last_run_at
    return out


@pytest.mark.asyncio
async def test_price_shock_force_reruns_only_scoped_specialists(database_url, session_factory):
    await ensure_checkpoint_tables(database_url)

    # Baseline: seed every cache row so off-scope reuse is exercised.
    before = await _seed_full_cache(session_factory, "AAPL")
    # Sleep enough that any rerun lands on a strictly-newer microsecond.
    await asyncio.sleep(0.01)

    analysis_id = str(uuid.uuid4())
    state = await run_analysis(
        symbol="AAPL",
        analysis_id=analysis_id,
        user_id="00000000-0000-0000-0000-000000000000",
        database_url=database_url,
        factory=session_factory,
        extra_context={
            "quick_refresh_event_type": "price_shock",
            "model_tier": "haiku",
        },
    )

    # In-scope for price_shock: technical, news, risk. The state keys mirror the
    # AnalysisState shape so news_sentiment/risk_preliminary/risk all appear.
    assert state.get("technical") is not None
    assert state.get("news_sentiment") is not None
    assert state.get("risk") is not None or state.get("risk_preliminary") is not None
    assert state.get("final") is not None
    assert state.get("bull") is not None
    assert state.get("bear") is not None

    # Cache: scoped specialists were re-run (newer last_run_at); off-scope kept theirs.
    after = _read_cache_timestamps(session_factory, "AAPL")
    in_scope = {"technical", "news", "risk"}
    for name in in_scope:
        assert after[name] > before[name], f"{name} should have been force-rerun"
    for name in set(before) - in_scope:
        assert after[name] == before[name], f"{name} should NOT have been re-run"

    # Persisted as QUICK_REFRESH.
    with session_scope(session_factory) as s:
        row = s.execute(select(Analysis).where(Analysis.analysis_id == analysis_id)).scalar_one()
        assert row.analysis_type == AnalysisType.QUICK_REFRESH.value
        assert row.status == "succeeded"


@pytest.mark.asyncio
async def test_earnings_release_force_reruns_fundamentals_valuation_news_risk(
    database_url, session_factory
):
    await ensure_checkpoint_tables(database_url)
    before = await _seed_full_cache(session_factory, "AAPL")
    await asyncio.sleep(0.01)

    analysis_id = str(uuid.uuid4())
    state = await run_analysis(
        symbol="AAPL",
        analysis_id=analysis_id,
        user_id="00000000-0000-0000-0000-000000000000",
        database_url=database_url,
        factory=session_factory,
        extra_context={
            "quick_refresh_event_type": "earnings_release",
            "model_tier": "haiku",
        },
    )
    # earnings_release scope (canonical): fundamentals, valuation, news, risk.
    assert state.get("fundamentals") is not None
    assert state.get("valuation") is not None
    assert state.get("news_sentiment") is not None
    assert state.get("final") is not None

    after = _read_cache_timestamps(session_factory, "AAPL")
    in_scope = {"fundamentals", "valuation", "news", "risk"}
    for name in in_scope:
        assert after[name] > before[name], f"{name} should have been force-rerun"
    for name in set(before) - in_scope:
        assert after[name] == before[name], f"{name} should NOT have been re-run"

    with session_scope(session_factory) as s:
        row = s.execute(select(Analysis).where(Analysis.analysis_id == analysis_id)).scalar_one()
        assert row.analysis_type == AnalysisType.QUICK_REFRESH.value
        assert row.status == "succeeded"

    _ = datetime.now(UTC)  # quiet the unused-import lint
