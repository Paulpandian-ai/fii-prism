"""Wiring test for the per-specialist tool-dispatch budget propagation.

Diagnosed bug: `_run_real` (and `_run_fake`) constructed `FundamentalsToolContext`
without passing `max_calls`, so the dataclass default of 15 silently shadowed the
cache_policy.call_cap("fundamentals") = 25 hard cap and the prompt-stated soft
budget of 22. Real-mode runs tripped on the inner 15-call cap at call 16,
mid-tool-loop, before the LLM could emit final JSON.

Fix: thread `max_calls=call_cap("fundamentals")` into both Fundamentals tool
contexts. This test asserts the values agree — i.e. the bug is structurally
prevented rather than masked by a behavior test.

Sibling specialists (News, Macro, Risk, Valuation, Moat, Technical, Insider)
have the same dual-budget pattern and almost certainly the same bug. They are
flagged in the commit message; this test does not cover them.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fii_agents.cache_policy import call_cap
from fii_agents.model import MODEL_SONNET, ModelCall
from fii_agents.specialists.base import SpecialistContext
from fii_agents.specialists.fundamentals import FundamentalsSpecialist
from fii_agents.tools.fundamentals import FundamentalsToolContext
from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult


class _Embedder:
    model = "shim"

    async def embed(self, texts, *, input_type: str = "document"):
        return EmbeddingResult(vectors=[[0.0] * EMBEDDING_DIM for _ in texts], model=self.model)


def _ctx(session_factory) -> SpecialistContext:
    return SpecialistContext(
        symbol="AAPL",
        analysis_id="00000000-0000-0000-0000-000000000000",
        user_id="00000000-0000-0000-0000-000000000000",
        factory=session_factory,
        embedder=_Embedder(),
        raw_bucket=None,
    )


def test_dataclass_default_is_a_smaller_budget_than_call_cap():
    """Sanity precondition for the bug: the dataclass default MUST be smaller
    than `call_cap("fundamentals")`, otherwise the diagnosed silent-shadowing
    failure mode couldn't have happened. If someone bumps the dataclass default
    to match call_cap, this test alerts you that the wiring fix can be deleted."""
    dataclass_default_ctx = FundamentalsToolContext(
        factory=None, embedder=None, raw_bucket=None
    )
    assert dataclass_default_ctx.max_calls < call_cap("fundamentals"), (
        "FundamentalsToolContext.max_calls dataclass default is no longer smaller "
        "than call_cap('fundamentals') — the wiring fix in _run_real / _run_fake "
        "may now be redundant."
    )


@pytest.mark.asyncio
async def test_run_fake_constructs_tool_ctx_with_call_cap_max_calls(session_factory):
    """_run_fake must propagate call_cap('fundamentals') into the tool context
    so the dispatch budget matches the outer specialist budget. Captured via a
    patch on FundamentalsToolContext."""
    captured: list[FundamentalsToolContext] = []
    real_ctor = FundamentalsToolContext

    def _spy(*args, **kwargs):
        instance = real_ctor(*args, **kwargs)
        captured.append(instance)
        return instance

    # Patch the symbol that fundamentals.py looks up when constructing the ctx.
    # We monkeypatch at the import-site (the specialist module) — patching
    # tools.fundamentals would not catch it because the specialist already
    # imported the class.
    with patch(
        "fii_agents.specialists.fundamentals.FundamentalsToolContext", side_effect=_spy
    ):
        await FundamentalsSpecialist()._run_fake(_ctx(session_factory))

    assert len(captured) == 1, "fake path should construct exactly one tool context"
    assert captured[0].max_calls == call_cap("fundamentals"), (
        f"fake path tool_ctx.max_calls={captured[0].max_calls} but "
        f"call_cap('fundamentals')={call_cap('fundamentals')} — wiring regressed"
    )


@pytest.mark.asyncio
async def test_run_real_constructs_tool_ctx_with_call_cap_max_calls(session_factory, monkeypatch):
    """_run_real must propagate call_cap('fundamentals') into the tool context.

    We force-disable fake mode and inject a Model that emits a single end_turn
    response immediately — no tools called — so the loop exits cleanly after
    constructing the context. The model's actual JSON output doesn't matter
    here; we only care whether the construction wired the budget correctly.
    """
    monkeypatch.delenv("FII_USE_FAKE_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-no-real-call")

    captured: list[FundamentalsToolContext] = []
    real_ctor = FundamentalsToolContext

    def _spy(*args, **kwargs):
        instance = real_ctor(*args, **kwargs)
        captured.append(instance)
        return instance

    class _ImmediateExitModel:
        """Returns an empty end_turn on the first call. The loop will try to
        try_parse(""), get a ReprompTicket, and re-prompt — but we only need
        the FIRST construction of FundamentalsToolContext, which happens
        BEFORE any model.respond call. So the model behavior is irrelevant
        for what we're asserting."""

        model_id = MODEL_SONNET
        is_fake = False

        async def respond(self, *, system, messages, tools=None, tool_choice=None):
            return ModelCall(
                text="",
                tool_calls=[],
                stop_reason="end_turn",
                tokens_in=10,
                tokens_out=2,
                model=MODEL_SONNET,
            )

    with patch(
        "fii_agents.specialists.fundamentals.FundamentalsToolContext", side_effect=_spy
    ):
        await FundamentalsSpecialist()._run_real(_ctx(session_factory), _ImmediateExitModel())

    assert len(captured) >= 1, "real path should construct at least one tool context"
    assert captured[0].max_calls == call_cap("fundamentals"), (
        f"real path tool_ctx.max_calls={captured[0].max_calls} but "
        f"call_cap('fundamentals')={call_cap('fundamentals')} — wiring regressed"
    )
