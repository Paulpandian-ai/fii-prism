"""Short-circuit on `stop_reason='max_tokens'`.

Background: when the Anthropic API truncates the response at `max_tokens`, the
JSON in `call.text` is malformed mid-string. Before this guard, the parse-retry
loop would fire and burn the full retry budget on the deterministic re-failure
(same input → same ceiling hit). The fix is two-part:

  (1) Bump the Model default `max_tokens` from 4096 → 16384 so realistic
      populated outputs no longer truncate.
  (2) Short-circuit the loop with `aborted_cap` (specialists) /
      `synthesis_invalid` (synthesis) when `stop_reason == "max_tokens"`,
      surfacing a discriminating error so retrying that path is impossible.

These tests pin both halves: a floor on the default budget, and a single
truncated response immediately ends the loop in fundamentals, llm_loop, and
synthesis.
"""

from __future__ import annotations

import pytest
from fii_agents.model import MODEL_SONNET, Model, ModelCall
from fii_agents.specialists.base import SpecialistContext
from fii_agents.specialists.fundamentals import FundamentalsSpecialist
from fii_agents.specialists.llm_loop import LoopParams, run_tool_loop
from fii_agents.specialists.synthesis import _real_synthesis
from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult
from fii_shared import MacroOutput


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
    """Disable fake mode: the truncation guard only fires in the real
    LLM-call paths. We don't actually hit the API; the Model is replaced
    with a scripted stand-in below."""
    monkeypatch.delenv("FII_USE_FAKE_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-no-real-call")


class _ScriptedModel:
    """Scripted Model stand-in: walks a fixed list of ModelCall responses."""

    model_id = MODEL_SONNET
    is_fake = False
    max_tokens = 16384

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


def _truncated_call(
    text: str = '{"symbol":"AAPL","revenue_ttm":{"value":391.0,"unit":"U',
) -> ModelCall:
    """A response that hit the max_tokens ceiling mid-string. The JSON is
    deliberately malformed to mirror the production bug exactly."""
    return ModelCall(
        text=text,
        tool_calls=[],
        stop_reason="max_tokens",
        tokens_in=2000,
        tokens_out=400,
        model=MODEL_SONNET,
    )


# --- 1. Default max_tokens floor -----------------------------------------------------------


def test_model_default_max_tokens_floor():
    """Default Model() must allocate enough output budget to fit a fully
    populated FundamentalsOutput / OrchestratorFinalOutput JSON. The floor
    (not equality) keeps this test from breaking on future bumps."""
    assert Model().max_tokens >= 8192, (
        f"Model() default max_tokens is {Model().max_tokens}, below the 8192 floor "
        "needed to fit the largest specialist output without truncation"
    )


# --- 2. Fundamentals._run_real short-circuits on max_tokens ---------------------------------


@pytest.mark.asyncio
async def test_fundamentals_short_circuits_on_max_tokens_truncation(session_factory):
    """A single response with stop_reason='max_tokens' must end the loop
    immediately with status='aborted_cap'. The parse-retry path MUST NOT fire
    (retrying the same input deterministically re-truncates)."""
    # Provide several extra slots so the test fails loudly with the scripted
    # AssertionError if the guard doesn't bind.
    model = _ScriptedModel([_truncated_call()] * 5)

    result = await FundamentalsSpecialist()._run_real(_ctx(session_factory), model)

    assert model.call_count == 1, (
        f"expected exactly 1 model call (truncation must short-circuit), got {model.call_count}"
    )
    assert result.status == "aborted_cap"
    assert result.output is None
    assert "output_truncated_at_max_tokens" in (result.error or "")
    # The error must reference the actual budget so operators can act on it.
    assert str(model.max_tokens) in (result.error or "")


# --- 3. llm_loop short-circuits on max_tokens (covers all 7 sibling specialists) -----------


@pytest.mark.asyncio
async def test_llm_loop_short_circuits_on_max_tokens_truncation():
    """Same guard in the shared runner used by Valuation, Moat, Macro,
    Technical, News, Insider, Risk, Bull, Bear. Driving run_tool_loop
    directly with a stub dispatch keeps the test small."""
    model = _ScriptedModel([_truncated_call()] * 5)

    async def _dispatch(name: str, input_: dict):
        raise AssertionError("dispatch must not be called when the response truncates")

    params = LoopParams(
        name="macro",
        system_prompt="test",
        user_message="test",
        tools=[],
        dispatch=_dispatch,
        output_schema=MacroOutput,
        max_iterations=20,
    )

    result = await run_tool_loop(model, params)

    assert model.call_count == 1, (
        f"expected exactly 1 model call (truncation must short-circuit), got {model.call_count}"
    )
    assert result.status == "aborted_cap"
    assert result.output is None
    assert "output_truncated_at_max_tokens" in (result.error or "")
    assert str(model.max_tokens) in (result.error or "")


# --- 4. Synthesis._real_synthesis short-circuits on max_tokens ------------------------------


@pytest.mark.asyncio
async def test_synthesis_short_circuits_on_max_tokens_truncation():
    """Synthesis has its own loop and the largest schema (worst blast radius
    on truncation). A single truncated response must short-circuit to
    synthesis_status='synthesis_invalid' with the truncation-specific
    placeholder thesis."""
    model = _ScriptedModel([_truncated_call()] * 5)

    state = {
        "symbol": "AAPL",
        "analysis_id": "00000000-0000-0000-0000-000000000000",
        "fundamentals": {"qualitative_summary": "stub"},
        "cost_running_total": 0.0,
        "tokens_in_total": 0,
        "tokens_out_total": 0,
        "model_calls_total": 0,
    }

    import time

    result = await _real_synthesis(state, model, time.perf_counter())

    assert model.call_count == 1, (
        f"expected exactly 1 model call (truncation must short-circuit), got {model.call_count}"
    )
    assert result["synthesis_status"] == "synthesis_invalid"
    final = result["final"]
    # Placeholder thesis must signal the truncation cause specifically — that's
    # how operators distinguish a max_tokens cutoff (fix: bump budget) from a
    # schema-validation failure (fix: prompt or upstream data).
    assert "truncated" in final["thesis"].lower()
    assert str(model.max_tokens) in final["thesis"]
