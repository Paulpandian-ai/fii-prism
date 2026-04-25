"""Final synthesis. Sonnet 4.6 by default; Opus 4.7 when use_premium=True.

Same fake-mode fallback pattern: when no API key, build the OrchestratorFinalOutput
deterministically from upstream specialist outputs (the Section-4 path).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from fii_shared import (
    CitedClaim,
    CostSummary,
    OrchestratorFinalOutput,
    SourceRef,
    SourceType,
)
from fii_shared.validation import ReprompTicket, try_parse

from fii_agents.budget import estimate_cost_usd
from fii_agents.model import MODEL_OPUS, MODEL_SONNET, Model
from fii_agents.prompts import SYNTHESIS_V1

log = structlog.get_logger(__name__)


# --- Public entrypoint --------------------------------------------------------------------


async def run_synthesis(state: dict[str, Any], *, use_premium: bool = False) -> dict[str, Any]:
    """Returns a dict ready to merge into LangGraph state."""
    started = time.perf_counter()
    if not state.get("symbol") or not state.get("analysis_id"):
        raise ValueError("synthesis requires symbol and analysis_id in state")

    model_id = MODEL_OPUS if use_premium else MODEL_SONNET
    model = Model(model_id=model_id)
    if model.is_fake:
        return _fake_synthesis(state, started)
    return await _real_synthesis(state, model, started)


# --- Fake (no LLM key) --------------------------------------------------------------------


def _fake_synthesis(state: dict[str, Any], started: float) -> dict[str, Any]:
    """Identical to the deterministic synthesis we shipped in Section 4."""
    fii_score = _score(state)
    rec = _rec_from_score(fii_score)
    # State now stores plain dicts (LangGraph's serializer requires JSON-primitive types).
    risk = state.get("risk") or state.get("risk_preliminary")
    stress = dict(risk.get("dfast_scenarios") or {}) if risk else {}

    src = SourceRef(
        source_type=SourceType.CALCULATED,
        source_id="synthesis/fake",
        section=None,
        retrieved_at=datetime.now(UTC),
        url=None,
    )
    wrong = [
        CitedClaim(
            claim="Fundamentals signal could decay if services growth slows.",
            sources=[src],
            confidence="medium",
        ),
        CitedClaim(
            claim="Regulatory outcomes could compress platform economics.",
            sources=[src],
            confidence="medium",
        ),
        CitedClaim(
            claim="A multiple de-rating could wipe out near-term upside.",
            sources=[src],
            confidence="low",
        ),
    ]
    summaries = {
        name: _first_sentence((state.get(name) or {}).get("qualitative_summary", "") or "")
        for name in (
            "fundamentals",
            "valuation",
            "moat",
            "macro",
            "technical",
            "news_sentiment",
            "insider_flow",
        )
        if state.get(name) is not None
    }
    what_buy = None
    if rec in ("buy", "strong_buy"):
        tech = state.get("technical") or {}
        risk_obj = state.get("risk") or state.get("risk_preliminary") or {}
        entry = float((tech.get("suggested_entry") or {}).get("value", 0.0))
        stop = float((tech.get("suggested_stop") or {}).get("value", 0.0))
        size = float(risk_obj.get("position_size_rec_pct", 0.0))
        what_buy = (
            f"Entry near [${entry:.2f}:technical.suggested_entry], "
            f"hard stop below [${stop:.2f}:technical.suggested_stop], "
            f"size at [{size:.1f}%:risk.position_size_rec_pct] of portfolio."
        )

    final = OrchestratorFinalOutput(
        symbol=state["symbol"].upper(),
        analysis_id=state["analysis_id"],
        recommendation=rec,
        confidence="medium",
        fii_score=float(fii_score),
        thesis=(
            f"{state['symbol'].upper()} fake-mode synthesis. Set ANTHROPIC_API_KEY for the "
            "real LLM-backed thesis that weighs Fundamentals + Valuation as primary inputs."
        ),
        what_i_would_buy=what_buy,
        what_could_make_me_wrong=wrong,
        time_horizon="long (years)",
        specialist_summaries=summaries,
        stress_outcomes=stress,
        cost_summary=CostSummary(
            total_usd=float(state.get("cost_running_total", 0.0)),
            input_tokens=int(state.get("tokens_in_total", 0)),
            output_tokens=int(state.get("tokens_out_total", 0)),
            model_calls=int(state.get("model_calls_total", 0)),
        ),
    )
    return {
        "final": final.model_dump(mode="json"),
        "timings_ms": {"synthesis": int((time.perf_counter() - started) * 1000)},
    }


# --- Real (LLM call) ----------------------------------------------------------------------


async def _real_synthesis(state: dict[str, Any], model: Model, started: float) -> dict[str, Any]:
    """One Sonnet/Opus call. We feed the full set of specialist outputs as JSON and let the
    model produce the OrchestratorFinalOutput. Re-prompted once on schema failure.
    """
    priors = {
        name: (obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj)
        for name, obj in state.items()
        if name
        in {
            "fundamentals",
            "valuation",
            "moat",
            "macro",
            "technical",
            "news_sentiment",
            "insider_flow",
            "risk_preliminary",
            "risk",
            "bull",
            "bear",
        }
        and obj is not None
    }

    user = (
        f"Synthesize the deep-dive for {state['symbol'].upper()}. The structured outputs of "
        "every specialist follow. Produce an OrchestratorFinalOutput as valid JSON.\n\n"
        f"<specialist_outputs>{json.dumps(priors, default=str)}</specialist_outputs>\n\n"
        f"analysis_id: {state['analysis_id']}\n"
        f"cost_so_far_usd: {float(state.get('cost_running_total', 0.0)):.4f}\n"
        f"tokens_in: {int(state.get('tokens_in_total', 0))}\n"
        f"tokens_out: {int(state.get('tokens_out_total', 0))}\n"
        f"model_calls: {int(state.get('model_calls_total', 0))}\n"
    )

    cost_usd = 0.0
    tokens_in = 0
    tokens_out = 0
    messages = [{"role": "user", "content": [{"type": "text", "text": user}]}]
    last = ""
    for _ in range(2):
        call = await model.respond(system=SYNTHESIS_V1.text, messages=messages, tools=None)
        cost_usd += estimate_cost_usd(call.model, call.tokens_in, call.tokens_out)
        tokens_in += call.tokens_in
        tokens_out += call.tokens_out
        last = call.text or last
        parsed = try_parse(OrchestratorFinalOutput, _extract_json(call.text))
        if isinstance(parsed, ReprompTicket):
            messages.append({"role": "assistant", "content": call.text})
            messages.append({"role": "user", "content": parsed.as_prompt()})
            continue
        # Patch in real cost summary now that we know the synthesis cost too.
        running = float(state.get("cost_running_total", 0.0)) + cost_usd
        running_in = int(state.get("tokens_in_total", 0)) + tokens_in
        running_out = int(state.get("tokens_out_total", 0)) + tokens_out
        running_calls = int(state.get("model_calls_total", 0)) + 1
        parsed = parsed.model_copy(
            update={
                "cost_summary": CostSummary(
                    total_usd=running,
                    input_tokens=running_in,
                    output_tokens=running_out,
                    model_calls=running_calls,
                )
            }
        )
        return {
            "final": parsed.model_dump(mode="json"),
            "cost_running_total": cost_usd,
            "tokens_in_total": tokens_in,
            "tokens_out_total": tokens_out,
            "model_calls_total": 1,
            "timings_ms": {"synthesis": int((time.perf_counter() - started) * 1000)},
        }

    # Two failures: fall back to deterministic fake.
    log.warning(
        "synthesis_real_failed_falling_back_to_fake",
        last_text=(last or "")[:300],
    )
    return _fake_synthesis(state, started)


# --- Helpers ------------------------------------------------------------------------------


def _extract_json(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if "\n" in s:
            first, rest = s.split("\n", 1)
            if first.strip().lower() in {"json", "json5"}:
                s = rest
    return s.strip()


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    for sep in (". ", "! ", "? "):
        if sep in text:
            return text.split(sep, 1)[0] + sep.strip()
    return text[:200]


def _score(state: dict) -> float:
    base = 5.0
    bumps = 0
    for name in (
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
        if state.get(name) is not None:
            bumps += 1
    return min(10.0, base + 0.5 * bumps)


def _rec_from_score(score: float) -> str:
    if score >= 8.5:
        return "strong_buy"
    if score >= 7.0:
        return "buy"
    if score >= 5.0:
        return "hold"
    if score >= 3.5:
        return "trim"
    return "sell"
