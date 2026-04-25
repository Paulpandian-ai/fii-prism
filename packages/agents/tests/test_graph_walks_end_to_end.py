"""Acceptance tests 1 through 4 plus 6 and 7.

Runs the full graph in fake-model mode against a real Postgres. Asserts:
  - All 10 specialist outputs populate
  - OrchestratorFinalOutput is valid and persisted
  - Cost is under $0.30 (0.0 in fake mode)
  - Stream yields events for every node
  - Filing-text wrapping policy is documented in tools (we can't test the real LLM's
    use of the tags without a key, but we CAN assert the tool wraps its output)
"""

from __future__ import annotations

import uuid

import pytest
from fii_agents import ensure_checkpoint_tables, run_analysis, stream_analysis
from fii_db import Analysis, AnalysisSpecialistOutput
from fii_db.session import session_scope
from sqlalchemy import select


@pytest.mark.asyncio
async def test_full_graph_walks_end_to_end(database_url, session_factory):
    await ensure_checkpoint_tables(database_url)
    analysis_id = str(uuid.uuid4())

    state = await run_analysis(
        symbol="AAPL",
        analysis_id=analysis_id,
        user_id="00000000-0000-0000-0000-000000000000",
        database_url=database_url,
        factory=session_factory,
    )

    # Acceptance 3: final output is a valid OrchestratorFinalOutput with real fundamentals.
    # State stores dicts (LangGraph serializer requirement); re-validate to confirm shape.
    from fii_shared import OrchestratorFinalOutput

    final_dict = state["final"]
    assert final_dict is not None
    final = OrchestratorFinalOutput.model_validate(final_dict)
    assert final.symbol == "AAPL"
    assert final.disclaimer == "For educational purposes only. Not investment advice."
    assert len(final.what_could_make_me_wrong) >= 3

    # Every specialist should be populated (either real or stub).
    for key in (
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
        assert state.get(key) is not None, f"{key} missing from state"

    # Acceptance 4: cost well under the hard cap ($0.30 stated; $1.00 default here).
    assert state.get("cost_running_total", 0.0) < 0.30

    # Acceptance 2: persist wrote both the Analysis row and one specialist row each.
    with session_scope(session_factory) as s:
        row = s.execute(select(Analysis).where(Analysis.analysis_id == analysis_id)).scalar_one()
        assert row.status == "succeeded"
        assert row.fii_score is not None

        specialist_rows = (
            s.execute(
                select(AnalysisSpecialistOutput).where(
                    AnalysisSpecialistOutput.analysis_id == analysis_id
                )
            )
            .scalars()
            .all()
        )
        assert len(specialist_rows) == 10


@pytest.mark.asyncio
async def test_stream_yields_node_events(database_url, session_factory):
    await ensure_checkpoint_tables(database_url)
    analysis_id = str(uuid.uuid4())

    events = []
    async for event in stream_analysis(
        symbol="AAPL",
        analysis_id=analysis_id,
        user_id="00000000-0000-0000-0000-000000000000",
        database_url=database_url,
        factory=session_factory,
    ):
        events.append(event)

    # Acceptance 2: the stream yields events per node.
    node_names = {e["node"] for e in events}
    expected = {
        "load_context",
        "fundamentals",
        "valuation",
        "moat",
        "macro",
        "technical",
        "news_sentiment",
        "insider_flow",
        "risk_preliminary",
        "bull",
        "bear",
        "risk_final",
        "synthesis",
        "persist",
    }
    missing = expected - node_names
    assert not missing, f"stream missing nodes: {missing}"


@pytest.mark.asyncio
async def test_filing_text_is_wrapped_in_sentinel_tags(session_factory):
    """Acceptance 7: the Fundamentals tools wrap filing text in <filing_text>...</filing_text>.

    We can't invoke the real LLM here, but we can call the tool directly with a symbol
    that has no filings on file — the tool should still return the sentinel wrapper
    shape (empty inside). The integration test for the wrapped-tag output runs when a
    real 10-K has been ingested.
    """
    from fii_agents.tools.fundamentals import (
        FILING_TEXT_CLOSE,
        FILING_TEXT_OPEN,
        FundamentalsToolContext,
        dispatch,
    )

    class _Noop:
        model = "noop"

        async def embed(self, texts, *, input_type: str = "document"):
            from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult

            return EmbeddingResult(vectors=[[0.0] * EMBEDDING_DIM for _ in texts], model=self.model)

    ctx = FundamentalsToolContext(factory=session_factory, embedder=_Noop(), raw_bucket=None)
    # Use a fake filing_id that won't exist; read_filing_section should still produce a
    # result with the sentinel tags around an empty body.
    result = await dispatch(
        "read_filing_section",
        {"filing_id": "00000000-0000-0000-0000-000000000000", "section_name": "Item 1"},
        ctx,
    )
    assert result["text"].startswith(FILING_TEXT_OPEN)
    assert result["text"].endswith(FILING_TEXT_CLOSE)
