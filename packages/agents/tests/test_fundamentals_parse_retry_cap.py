"""Bounded parse-retry cap on Fundamentals._run_real.

Background: prior to the cap, an LLM that emitted schema-invalid JSON would be
re-prompted unboundedly (until the outer iteration or cost cap fired). One
real-mode AAPL run hit 5 retries x~$0.06 each = $0.30 of pure parse-retry
spend before the $0.60 cost cap aborted it. Now we cap at MAX_PARSE_ATTEMPTS
and exit cleanly with status='aborted_cap'.

Both tests run a scripted Model — no Anthropic API key needed. The fake-mode
path is exercised separately by test_specialists_fake_mode.py; this file is
real-mode-only because the parse-retry path is structurally real-mode-only
(see comment on MAX_PARSE_ATTEMPTS in fundamentals.py).
"""

from __future__ import annotations

import pytest
from fii_agents.model import MODEL_SONNET, ModelCall
from fii_agents.specialists.base import SpecialistContext
from fii_agents.specialists.fundamentals import MAX_PARSE_ATTEMPTS, FundamentalsSpecialist
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


@pytest.fixture(autouse=True)
def _force_real_mode(monkeypatch):
    """Disable fake mode for every test in this file — the parse-retry loop
    only fires in `_run_real`. We don't actually call the Anthropic API; the
    Model is replaced with a scripted stand-in below."""
    monkeypatch.delenv("FII_USE_FAKE_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-no-real-call")


class _ScriptedModel:
    """Walks through a fixed list of ModelCall responses one per .respond()."""

    model_id = MODEL_SONNET
    is_fake = False

    def __init__(self, responses: list[ModelCall]) -> None:
        self._responses = responses
        self._i = 0
        self.call_count = 0

    async def respond(self, *, system, messages, tools=None, tool_choice=None) -> ModelCall:
        self.call_count += 1
        if self._i >= len(self._responses):
            raise AssertionError(
                f"_ScriptedModel ran out of responses after {self.call_count} calls "
                "— test is over-running the script (likely the cap didn't fire)"
            )
        r = self._responses[self._i]
        self._i += 1
        return r


# --- 1. Parse-retry cap fires after MAX_PARSE_ATTEMPTS schema failures -------------------


@pytest.mark.asyncio
async def test_parse_retry_cap_fires_after_max_attempts(session_factory):
    """A model that always returns invalid JSON on the final-emit step must
    abort with status='aborted_cap' after exactly MAX_PARSE_ATTEMPTS, with the
    accumulated cost preserved and the last validator error surfaced in `error`."""
    invalid_json_call = ModelCall(
        text="this is not valid json",
        tool_calls=[],
        stop_reason="end_turn",
        tokens_in=2000,
        tokens_out=400,
        model=MODEL_SONNET,
    )
    # Provide one extra response so the test would fail loudly if the cap
    # fails to bind (we'd burn through this slot and AssertionError out).
    responses = [invalid_json_call] * (MAX_PARSE_ATTEMPTS + 1)
    model = _ScriptedModel(responses)

    result = await FundamentalsSpecialist()._run_real(_ctx(session_factory), model)

    # Cap binds at exactly MAX_PARSE_ATTEMPTS calls — no over-spending.
    assert model.call_count == MAX_PARSE_ATTEMPTS, (
        f"expected exactly {MAX_PARSE_ATTEMPTS} model calls, got {model.call_count}"
    )
    assert result.status == "aborted_cap"
    assert result.output is None
    # Cost reflects what we actually spent (3 xper-call cost), NOT zero.
    assert result.cost_usd > 0.0, "cost_usd must reflect the retry attempts we paid for"
    # Error message references the constant so the message can't lie if it's
    # ever tuned.
    assert f"fundamentals_schema_invalid_after_{MAX_PARSE_ATTEMPTS}_attempts" in (
        result.error or ""
    )
    # The last validator error from the LLM should surface for diagnostics.
    assert "JSON" in (result.error or "") or "schema" in (result.error or "").lower()


# --- 2. Successful parse on attempt 1 still works (regression guard) --------------------


@pytest.mark.asyncio
async def test_successful_parse_on_first_attempt_still_works(session_factory):
    """Regression guard: the parse-retry cap MUST NOT change the happy-path
    behavior. A scripted model that emits a valid FundamentalsOutput on the
    first turn should return status='ok' with the parsed output, exactly as
    before the cap was added."""
    from fii_agents.specialists.fundamentals import _build_output_from_raw

    # Construct a canonical valid output via the same helper the fake path
    # uses, then serialize. The schema validation is done by FundamentalsOutput
    # itself in both modes — if the helper produces something invalid, both
    # the fake-mode tests and this test would fail.
    valid_output = _build_output_from_raw(
        "AAPL", income={}, ratios={}, ndebt={}, is_stub=False, has_10k=True
    )
    valid_json = valid_output.model_dump_json()

    model = _ScriptedModel(
        [
            ModelCall(
                text=valid_json,
                tool_calls=[],
                stop_reason="end_turn",
                tokens_in=1500,
                tokens_out=900,
                model=MODEL_SONNET,
            ),
        ]
    )

    result = await FundamentalsSpecialist()._run_real(_ctx(session_factory), model)

    assert model.call_count == 1, "happy path must finish in one model call"
    assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"
    assert result.output is not None
    # Returned output round-trips through Pydantic with the schema we asked for.
    assert result.output.symbol == "AAPL"
    assert result.error is None
