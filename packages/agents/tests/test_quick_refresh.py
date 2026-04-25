"""Section 7: quick_refresh mode narrows the preliminary specialists to the
event-type scope, still produces a valid final, and is persisted as QUICK_REFRESH."""

from __future__ import annotations

import uuid

import pytest
from fii_agents import ensure_checkpoint_tables, run_analysis
from fii_db import Analysis, AnalysisType
from fii_db.session import session_scope
from sqlalchemy import select


@pytest.mark.asyncio
async def test_price_shock_skips_off_scope_specialists(database_url, session_factory):
    await ensure_checkpoint_tables(database_url)
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

    # In-scope for price_shock: technical, news_sentiment, risk_preliminary.
    assert state.get("technical") is not None
    assert state.get("news_sentiment") is not None
    assert state.get("risk_preliminary") is not None

    # Out-of-scope preliminaries should NOT have produced output.
    assert state.get("fundamentals") is None
    assert state.get("valuation") is None
    assert state.get("moat") is None
    assert state.get("macro") is None
    assert state.get("insider_flow") is None

    # Bull / bear / risk_final / synthesis / persist always run.
    assert state.get("bull") is not None
    assert state.get("bear") is not None
    assert state.get("risk") is not None
    assert state.get("final") is not None

    # Persisted as QUICK_REFRESH.
    with session_scope(session_factory) as s:
        row = s.execute(select(Analysis).where(Analysis.analysis_id == analysis_id)).scalar_one()
        assert row.analysis_type == AnalysisType.QUICK_REFRESH.value
        assert row.status == "succeeded"


@pytest.mark.asyncio
async def test_earnings_release_runs_fundamentals_and_valuation(database_url, session_factory):
    await ensure_checkpoint_tables(database_url)
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
    # earnings_release scope: fundamentals, valuation, news_sentiment, risk_preliminary
    assert state.get("fundamentals") is not None
    assert state.get("valuation") is not None
    assert state.get("news_sentiment") is not None
    # Not in scope
    assert state.get("moat") is None
    assert state.get("macro") is None
    assert state.get("technical") is None
    assert state.get("insider_flow") is None
