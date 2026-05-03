"""Section 11 / Phase-1 acceptance tests for the per-specialist cache + synthesis-from-cache pipeline.

Three scenarios are covered here:

1. Cache hit on second run — invoking the fundamentals specialist twice for the
   same symbol returns a `from_cache=True` result the second time and does NOT
   construct a new specialist instance. `force=True` bypasses the cache.

2. Synthesize-blocked-by-missing — calling `synthesize_from_cache` when one
   required specialist is absent raises `MissingSpecialists` listing the names.

3. Synthesis_invalid after the attempt cap — when synthesis returns malformed
   JSON for `synthesis_max_attempts()` calls in a row, the result status is
   `synthesis_invalid` and spending stops at exactly the cap (no extra calls).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fii_agents.cache_policy import synthesis_max_attempts
from fii_agents.specialist_runner import run_specialist
from fii_agents.specialists.synthesis import _real_synthesis
from fii_agents.synthesis_runner import MissingSpecialists, synthesize_from_cache
from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult
from fii_db import SpecialistCache, Ticker
from fii_db.session import session_scope
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

# --- Helpers ----------------------------------------------------------------------------


class _Embedder:
    model = "shim"

    async def embed(self, texts, *, input_type: str = "document"):
        return EmbeddingResult(vectors=[[0.0] * EMBEDDING_DIM for _ in texts], model=self.model)


def _wipe_cache(factory, symbol: str) -> None:
    with session_scope(factory) as s:
        s.execute(delete(SpecialistCache).where(SpecialistCache.symbol == symbol))


def _seed_ticker(factory, symbol: str) -> None:
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )


def _seed_cache_row(
    factory, *, symbol: str, name: str, output: dict, last_run_at: datetime | None = None
) -> None:
    """Insert a fresh, status='ok' cache row for the given specialist."""
    _seed_ticker(factory, symbol)
    values = {
        "symbol": symbol,
        "specialist_name": name,
        "output_json": output,
        "reasoning_text": None,
        "citations_json": {},
        "model_used": "fake",
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0,
        "duration_ms": 0,
        "status": "ok",
        "last_run_at": last_run_at or datetime.now(UTC),
        "last_input_hash": "test",
    }
    stmt = pg_insert(SpecialistCache).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SpecialistCache.symbol, SpecialistCache.specialist_name],
        set_={k: v for k, v in values.items() if k not in ("symbol", "specialist_name")},
    )
    with session_scope(factory) as s:
        s.execute(stmt)


# --- 1. Cache hit on second run ---------------------------------------------------------


@pytest.mark.asyncio
async def test_run_specialist_cache_hit_on_second_call(session_factory):
    """Second call returns the cached row instead of re-running fake mode."""
    sym = "TST1"
    _wipe_cache(session_factory, sym)
    _seed_ticker(session_factory, sym)

    first = await run_specialist(
        "fundamentals",
        symbol=sym,
        factory=session_factory,
        embedder=_Embedder(),
    )
    assert first.from_cache is False
    assert first.status == "ok"
    assert first.output is not None
    first_last_run = first.last_run_at

    second = await run_specialist(
        "fundamentals",
        symbol=sym,
        factory=session_factory,
        embedder=_Embedder(),
    )
    assert second.from_cache is True
    assert second.status == "ok"
    # Cache hit returns the row's last_run_at, not a fresh datetime.
    assert second.last_run_at == first_last_run

    # force=True bypasses cache and triggers a new run.
    third = await run_specialist(
        "fundamentals",
        symbol=sym,
        factory=session_factory,
        embedder=_Embedder(),
        force=True,
    )
    assert third.from_cache is False
    assert third.last_run_at >= first_last_run

    _wipe_cache(session_factory, sym)


# --- 2. Synthesize blocked by missing specialists ---------------------------------------


@pytest.mark.asyncio
async def test_synthesize_blocked_when_specialists_missing(session_factory):
    sym = "TST2"
    _wipe_cache(session_factory, sym)
    # Seed 7 of the 8 — leave 'risk' missing.
    minimal_output = {"qualitative_summary": "stub"}
    for name in (
        "fundamentals",
        "valuation",
        "moat",
        "macro",
        "technical",
        "news",
        "insider",
    ):
        _seed_cache_row(session_factory, symbol=sym, name=name, output=minimal_output)

    with pytest.raises(MissingSpecialists) as excinfo:
        await synthesize_from_cache(
            sym,
            factory=session_factory,
            embedder=_Embedder(),
        )
    assert "risk" in excinfo.value.missing
    assert excinfo.value.stale == []

    # Now seed risk but make it stale (> 1 day TTL for risk).
    stale_ts = datetime.now(UTC) - timedelta(days=3)
    _seed_cache_row(
        session_factory, symbol=sym, name="risk", output=minimal_output, last_run_at=stale_ts
    )
    with pytest.raises(MissingSpecialists) as excinfo:
        await synthesize_from_cache(
            sym,
            factory=session_factory,
            embedder=_Embedder(),
        )
    assert "risk" in excinfo.value.stale
    assert excinfo.value.missing == []

    _wipe_cache(session_factory, sym)


# --- 3. Synthesis hits the 3-attempt JSON-validity cap ----------------------------------


@pytest.mark.asyncio
async def test_synthesis_invalid_status_after_attempt_cap(session_factory, monkeypatch):
    """A model that always returns invalid JSON exhausts the cap, reports
    `synthesis_invalid`, and STOPS calling after exactly `synthesis_max_attempts()`.
    """
    from fii_agents.model import MODEL_SONNET, ModelCall

    cap = synthesis_max_attempts()
    call_counter = {"n": 0}

    class _AlwaysInvalidModel:
        model_id = MODEL_SONNET
        is_fake = False

        async def respond(self, *, system, messages, tools=None):
            call_counter["n"] += 1
            return ModelCall(
                text="this is not valid json",
                tool_calls=[],
                stop_reason="end_turn",
                tokens_in=10,
                tokens_out=4,
                model=MODEL_SONNET,
            )

    state = {
        "symbol": "TST3",
        "analysis_id": str(uuid.uuid4()),
        "fundamentals": {"qualitative_summary": "stub"},
    }
    delta = await _real_synthesis(state, _AlwaysInvalidModel(), started=0.0)
    assert delta["synthesis_status"] == "synthesis_invalid"
    assert call_counter["n"] == cap, (
        f"expected exactly {cap} calls (the cap); got {call_counter['n']}"
    )
    # The placeholder is a valid OrchestratorFinalOutput with low confidence.
    final = delta["final"]
    assert final["recommendation"] == "hold"
    assert final["confidence"] == "low"
    assert final["symbol"] == "TST3"


# --- 4. Fundamentals fake-path with no 10-K ingested ------------------------------------


@pytest.mark.asyncio
async def test_fundamentals_fake_completes_with_medium_confidence_when_no_10k(session_factory):
    """A symbol with no filings on file should still produce a valid Fundamentals
    output. Confidence drops to 'medium' (down from 'high'-equivalent with-10K)
    and an auditor_flag explicitly notes the missing 10-K so synthesis sees the gap.
    """
    from fii_agents.model import Model
    from fii_agents.specialists.base import SpecialistContext
    from fii_agents.specialists.fundamentals import FundamentalsSpecialist

    sym = "TST10K"
    _wipe_cache(session_factory, sym)
    _seed_ticker(session_factory, sym)
    # Confirm the symbol genuinely has no 10-K on file.
    from fii_db import Filing

    with session_scope(session_factory) as s:
        s.execute(delete(Filing).where(Filing.symbol == sym))

    ctx = SpecialistContext(
        symbol=sym,
        analysis_id="00000000-0000-0000-0000-000000000000",
        user_id="00000000-0000-0000-0000-000000000000",
        factory=session_factory,
        embedder=_Embedder(),
        raw_bucket=None,
    )
    result = await FundamentalsSpecialist().run(ctx, Model())  # FII_USE_FAKE_MODEL=1

    assert result.error is None
    assert result.output is not None
    out = result.output
    assert out.confidence == "medium"
    flag_claims = [f.claim for f in out.auditor_flags]
    assert any(c.startswith("10-K not ingested") for c in flag_claims), (
        f"expected an auditor_flag noting the missing 10-K; got {flag_claims}"
    )
    # The qualitative summary should also surface the caveat.
    assert "10-K not ingested" in (out.qualitative_summary or "")


# --- 5. Cap-hit cost is preserved in the cache row --------------------------------------


@pytest.mark.asyncio
async def test_fundamentals_cost_persisted_when_call_cap_hits(session_factory, monkeypatch):
    """When the per-specialist call cap kicks in, the SpecialistResult and the
    cache row must reflect the partial spend — not 0.0. Bug from Phase 1: the
    runner was hard-coding cost_usd=0 on aborted_cap rows.
    """
    from fii_agents.budget import estimate_cost_usd
    from fii_agents.model import MODEL_SONNET, ModelCall
    from fii_agents.specialists.base import SpecialistContext
    from fii_agents.specialists.fundamentals import FundamentalsSpecialist

    sym = "TSTCAP"
    _wipe_cache(session_factory, sym)
    _seed_ticker(session_factory, sym)

    # Constrain the cap so we hit it fast. Force-disable fake mode for this test
    # so _run_real is exercised.
    monkeypatch.setenv("FII_CAP_CALLS_FUNDAMENTALS", "3")
    monkeypatch.delenv("FII_USE_FAKE_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-no-real-call")

    class _ToolUseForeverModel:
        """Always asks for a tool call (`get_income_statement`) so the loop never
        finishes producing JSON. Reports non-zero token usage per call so cost
        accumulates."""

        model_id = MODEL_SONNET
        is_fake = False

        async def respond(self, *, system, messages, tools=None, tool_choice=None):
            return ModelCall(
                text="",
                tool_calls=[
                    {
                        "id": f"call_{len(messages)}",
                        "name": "get_income_statement",
                        "input": {"symbol": "TSTCAP", "periods": 4},
                    }
                ],
                stop_reason="tool_use",
                tokens_in=500,
                tokens_out=120,
                model=MODEL_SONNET,
            )

    ctx = SpecialistContext(
        symbol=sym,
        analysis_id="00000000-0000-0000-0000-000000000000",
        user_id="00000000-0000-0000-0000-000000000000",
        factory=session_factory,
        embedder=_Embedder(),
        raw_bucket=None,
    )
    result = await FundamentalsSpecialist().run(ctx, _ToolUseForeverModel())

    expected_per_call = estimate_cost_usd(MODEL_SONNET, 500, 120)
    assert result.status == "aborted_cap", f"expected aborted_cap, got {result.status}"
    assert result.cost_usd > 0.0, "result.cost_usd should be > 0 after cap hits"
    # Three calls, each with non-zero cost; total should be roughly 3 * per-call.
    assert result.cost_usd >= 0.99 * 3 * expected_per_call
    assert result.tokens_in == 3 * 500
    assert result.tokens_out == 3 * 120

    # Now persist via run_specialist's _upsert_cache pathway and verify the cache row.
    from fii_agents.specialist_runner import _upsert_cache

    _upsert_cache(
        session_factory,
        sym,
        "fundamentals",
        output_json={},
        reasoning_text=result.error,
        cost_usd=result.cost_usd,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        duration_ms=result.duration_ms,
        status=result.status,
        model_used=MODEL_SONNET,
    )
    with session_scope(session_factory) as s:
        row = s.execute(
            select(SpecialistCache).where(
                SpecialistCache.symbol == sym,
                SpecialistCache.specialist_name == "fundamentals",
            )
        ).scalar_one()
        assert row.status == "aborted_cap"
        assert float(row.cost_usd) > 0.0, "cache row cost_usd must reflect partial spend"
        assert row.tokens_in == 3 * 500
        assert row.tokens_out == 3 * 120

    _wipe_cache(session_factory, sym)


# --- 6. Fundamentals normal run completes under the new $0.60 budget --------------------


@pytest.mark.asyncio
async def test_fundamentals_default_cap_is_60_cents_and_normal_run_completes_within(
    session_factory, monkeypatch
):
    """Sanity check on the new tiered cost-cap defaults plus an end-to-end
    real-path simulation that finishes WITHIN the $0.60 fundamentals budget.

    The mock model emits four tool-use turns (a realistic 'plan + read income +
    read ratios + finalize' shape) then a valid FundamentalsOutput. Each call
    burns Sonnet-tier tokens (~1.5K in / 300 out) which approximates a real
    run; the test asserts the loop returns status='ok' with a cost well under
    the new $0.60 cap.
    """
    from fii_agents.budget import estimate_cost_usd
    from fii_agents.cache_policy import _DEFAULT_COST_CAP_USD, cost_cap_usd
    from fii_agents.model import MODEL_SONNET, ModelCall
    from fii_agents.specialists.base import SpecialistContext
    from fii_agents.specialists.fundamentals import (
        FundamentalsSpecialist,
        _build_output_from_raw,
    )

    # 1. Defaults table reflects the requested tiers.
    assert cost_cap_usd("fundamentals") == 0.60
    assert cost_cap_usd("moat") == 0.40
    assert cost_cap_usd("valuation") == 0.40
    assert cost_cap_usd("synthesis") == 0.40
    for q in ("news", "macro", "technical", "insider", "risk"):
        assert cost_cap_usd(q) == 0.25, q
    # The mapping is exhaustive — no specialist falls back to the global default.
    assert set(_DEFAULT_COST_CAP_USD).issuperset(
        {"fundamentals", "moat", "valuation", "synthesis", "news", "macro", "technical",
         "insider", "risk"}
    )

    # 2. Env override on the new var name takes precedence.
    monkeypatch.setenv("FII_COST_CAP_FUNDAMENTALS", "1.23")
    assert cost_cap_usd("fundamentals") == 1.23
    monkeypatch.delenv("FII_COST_CAP_FUNDAMENTALS")

    # 3. Real-path run simulation — never hits the cap on a normal flow.
    sym = "TSTBUDGET"
    _wipe_cache(session_factory, sym)
    _seed_ticker(session_factory, sym)
    monkeypatch.delenv("FII_USE_FAKE_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-no-real-call")

    valid_output = _build_output_from_raw(
        sym, income={}, ratios={}, ndebt={}, is_stub=False, has_10k=True
    )
    valid_json = valid_output.model_dump_json()

    turns = [
        ModelCall(
            text="Planning. I'll start with the income statement.",
            tool_calls=[
                {"id": "c1", "name": "get_income_statement", "input": {"symbol": sym, "periods": 8}}
            ],
            stop_reason="tool_use",
            tokens_in=1500,
            tokens_out=300,
            model=MODEL_SONNET,
        ),
        ModelCall(
            text="Now ratios.",
            tool_calls=[
                {"id": "c2", "name": "get_ratios", "input": {"symbol": sym, "periods": 4}}
            ],
            stop_reason="tool_use",
            tokens_in=1600,
            tokens_out=280,
            model=MODEL_SONNET,
        ),
        ModelCall(
            text="Net debt now.",
            tool_calls=[
                {"id": "c3", "name": "calculate_net_debt_to_ebitda", "input": {"symbol": sym}}
            ],
            stop_reason="tool_use",
            tokens_in=1700,
            tokens_out=240,
            model=MODEL_SONNET,
        ),
        ModelCall(
            text=valid_json,
            tool_calls=[],
            stop_reason="end_turn",
            tokens_in=1900,
            tokens_out=900,
            model=MODEL_SONNET,
        ),
    ]
    expected_cost = sum(estimate_cost_usd(c.model, c.tokens_in, c.tokens_out) for c in turns)

    class _ScriptedModel:
        model_id = MODEL_SONNET
        is_fake = False

        def __init__(self) -> None:
            self._i = 0

        async def respond(self, *, system, messages, tools=None, tool_choice=None):
            call = turns[self._i]
            self._i += 1
            return call

    ctx = SpecialistContext(
        symbol=sym,
        analysis_id="00000000-0000-0000-0000-000000000000",
        user_id="00000000-0000-0000-0000-000000000000",
        factory=session_factory,
        embedder=_Embedder(),
        raw_bucket=None,
    )
    result = await FundamentalsSpecialist().run(ctx, _ScriptedModel())

    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"
    assert result.error is None
    assert result.output is not None
    assert result.cost_usd == pytest.approx(expected_cost, rel=1e-6)
    assert result.cost_usd < cost_cap_usd("fundamentals"), (
        f"normal run cost ${result.cost_usd:.4f} should be < ${cost_cap_usd('fundamentals'):.2f}"
    )
    assert result.model_calls == 4

    _wipe_cache(session_factory, sym)
