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
from sqlalchemy import delete
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
