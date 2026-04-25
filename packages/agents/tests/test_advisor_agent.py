"""Section 8: advisor agent fake-mode behaviour + stream frame ordering."""

from __future__ import annotations

import pytest
from fii_agents import AdvisorToolContext, Model, run_advisor_turn, stream_advisor_turn
from fii_db.models import SINGLE_USER_ID


@pytest.mark.asyncio
async def test_advisor_turn_fake_mode_includes_disclaimer(session_factory):
    ctx = AdvisorToolContext(factory=session_factory, user_id=SINGLE_USER_ID)
    model = Model()  # fake mode (FII_USE_FAKE_MODEL=1 fixture)
    turn = await run_advisor_turn(
        model=model,
        history=[],
        user_message="Can I keep XOM?",
        tool_ctx=ctx,
    )
    assert "For educational purposes only. Not investment advice." in turn.text
    assert turn.cost_usd == 0.0
    assert turn.model_used == "fake"


@pytest.mark.asyncio
async def test_advisor_stream_emits_message_complete(session_factory):
    ctx = AdvisorToolContext(factory=session_factory, user_id=SINGLE_USER_ID)
    model = Model()
    frames = []
    async for f in stream_advisor_turn(
        model=model,
        history=[],
        user_message="Can I keep XOM?",
        tool_ctx=ctx,
    ):
        frames.append(f)
    kinds = [f.kind for f in frames]
    # Fake mode bypasses tool_use; we expect just the message_complete frame.
    assert "message_complete" in kinds
    final = next(f for f in frames if f.kind == "message_complete")
    assert "Not investment advice" in (final.final_text or "")
