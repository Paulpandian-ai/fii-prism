"""Generic Claude tool-use loop, factored out so every specialist uses the same pattern.

Each specialist supplies:
- system prompt (loaded from agent_prompts)
- initial user message
- tools (JSON Schemas) + dispatch coroutine
- output schema (Pydantic model)

The loop handles: tool routing, schema validation via try_parse, single-shot
re-prompt on schema failure, max-iteration cap, cost accounting.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import structlog
from fii_shared.validation import ReprompTicket, try_parse
from pydantic import BaseModel

from fii_agents.budget import estimate_cost_usd, estimate_input_tokens
from fii_agents.model import Model
from fii_agents.specialists.base import SpecialistResult

# Pre-flight gate: if estimated input cost exceeds this fraction of the
# specialist's cost cap, abort cleanly with status='input_too_large' BEFORE
# sending the request. The remaining 40% reserves room for the model's output
# tokens, which carry the higher per-token rate.
INPUT_COST_CAP_FRACTION = 0.60

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

DispatchFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class LoopParams:
    name: str  # specialist name (for logging)
    system_prompt: str
    user_message: str
    tools: list[dict[str, Any]]
    dispatch: DispatchFn
    output_schema: type[BaseModel]
    max_iterations: int = 20
    tool_choice: dict[str, Any] | None = None
    # Per-specialist hard caps. None disables the cap. The loop checks BEFORE each
    # model.respond() call: if either cap is at-or-over the limit, it stops and
    # returns a SpecialistResult with status="aborted_cap" preserving whatever
    # tokens/cost/calls we'd accumulated.
    max_calls: int | None = None
    max_cost_usd: float | None = None


def _extract_json(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if "\n" in s:
            first, rest = s.split("\n", 1)
            if first.strip().lower() in {"json", "json5"}:
                s = rest
    return s.strip()


async def run_tool_loop(model: Model, p: LoopParams) -> SpecialistResult:
    """Drive the assistant <-> tool loop until the model emits a valid JSON output
    or we exhaust the iteration budget."""
    if model.is_fake:
        raise RuntimeError(
            "run_tool_loop called in fake mode; specialists should branch on model.is_fake."
        )

    # Backfill per-specialist caps from cache_policy if the caller didn't set them.
    # This wires the 25-call / $0.30 default caps to every specialist that uses
    # run_tool_loop without each one having to import cache_policy explicitly.
    if p.max_calls is None or p.max_cost_usd is None:
        from fii_agents.cache_policy import call_cap, cost_cap_usd

        if p.max_calls is None:
            p.max_calls = call_cap(p.name)
        if p.max_cost_usd is None:
            p.max_cost_usd = cost_cap_usd(p.name)

    started = time.perf_counter()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": p.user_message}]}
    ]
    tokens_in_total = 0
    tokens_out_total = 0
    model_calls = 0
    cost_usd = 0.0
    last_text = ""

    # Wrap the whole loop so a transient API error or tool exception still surfaces
    # a SpecialistResult carrying the partial cost we accumulated — without this,
    # the caller's exception handler would persist cost_usd=0 in the cache row.
    try:
        for _ in range(p.max_iterations):
            # Per-specialist hard caps. Checked BEFORE the next call so we never blow
            # past the limit by 1 call / by the cost of one extra invocation.
            if p.max_calls is not None and model_calls >= p.max_calls:
                return _aborted_cap(
                    p.name,
                    started,
                    tokens_in_total,
                    tokens_out_total,
                    cost_usd,
                    model_calls,
                    last_text,
                    reason=f"call_cap_{p.max_calls}",
                )
            if p.max_cost_usd is not None and cost_usd >= p.max_cost_usd:
                return _aborted_cap(
                    p.name,
                    started,
                    tokens_in_total,
                    tokens_out_total,
                    cost_usd,
                    model_calls,
                    last_text,
                    reason=f"cost_cap_${p.max_cost_usd:.2f}",
                )

            # Pre-flight cost estimate: refuse to send a request whose input alone
            # would already exceed INPUT_COST_CAP_FRACTION of the specialist's cap.
            if p.max_cost_usd is not None:
                est_in_tokens = estimate_input_tokens(messages, system=p.system_prompt)
                est_input_cost = estimate_cost_usd(model.model_id, est_in_tokens, 0)
                threshold = INPUT_COST_CAP_FRACTION * p.max_cost_usd
                if est_input_cost > threshold:
                    return _input_too_large(
                        p.name,
                        started,
                        tokens_in_total,
                        tokens_out_total,
                        cost_usd,
                        model_calls,
                        est_in_tokens,
                        est_input_cost,
                        threshold,
                        p.max_cost_usd,
                    )

            call = await model.respond(
                system=p.system_prompt, messages=messages, tools=p.tools, tool_choice=p.tool_choice
            )
            model_calls += 1
            tokens_in_total += call.tokens_in
            tokens_out_total += call.tokens_out
            cost_usd += estimate_cost_usd(call.model, call.tokens_in, call.tokens_out)
            last_text = call.text or last_text

            if call.stop_reason == "tool_use" and call.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            *([{"type": "text", "text": call.text}] if call.text else []),
                            *[
                                {
                                    "type": "tool_use",
                                    "id": tc["id"],
                                    "name": tc["name"],
                                    "input": tc["input"],
                                }
                                for tc in call.tool_calls
                            ],
                        ],
                    }
                )
                results_block: dict[str, Any] = {"role": "user", "content": []}
                for tc in call.tool_calls:
                    tool_result = await p.dispatch(tc["name"], tc["input"])
                    results_block["content"].append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": json.dumps(tool_result, default=str),
                        }
                    )
                messages.append(results_block)
                continue

            # Anthropic-side truncation: the response hit max_tokens BEFORE
            # the model finished emitting JSON. Retrying is pure waste — the
            # same input produces the same ceiling hit. Reuses aborted_cap
            # for taxonomy consistency. See specialists/fundamentals.py for
            # the matching guard. This single guard covers every specialist
            # routed through this shared runner (Valuation, Moat, Macro,
            # Technical, News, Insider, Risk, Bull, Bear).
            if call.stop_reason == "max_tokens":
                duration_ms = int((time.perf_counter() - started) * 1000)
                log.warning(
                    "output_truncated_at_max_tokens",
                    specialist=p.name,
                    max_tokens=model.max_tokens,
                    last_text=(call.text or "")[:300],
                    cost_usd=round(cost_usd, 6),
                )
                return SpecialistResult(
                    output=None,
                    tokens_in=tokens_in_total,
                    tokens_out=tokens_out_total,
                    cost_usd=cost_usd,
                    model_calls=model_calls,
                    duration_ms=duration_ms,
                    error=(
                        f"output_truncated_at_max_tokens={model.max_tokens}: "
                        "model.respond returned stop_reason='max_tokens' before "
                        "emitting valid JSON"
                    ),
                    status="aborted_cap",
                )

            parsed = try_parse(p.output_schema, _extract_json(call.text))
            if isinstance(parsed, ReprompTicket):
                messages.append({"role": "assistant", "content": call.text})
                messages.append({"role": "user", "content": parsed.as_prompt()})
                continue

            duration_ms = int((time.perf_counter() - started) * 1000)
            log.info(
                "specialist_real_complete",
                specialist=p.name,
                iterations=model_calls,
                duration_ms=duration_ms,
                cost_usd=round(cost_usd, 6),
            )
            return SpecialistResult(
                output=parsed,
                tokens_in=tokens_in_total,
                tokens_out=tokens_out_total,
                cost_usd=cost_usd,
                model_calls=model_calls,
                duration_ms=duration_ms,
            )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.exception(
            "specialist_real_exception",
            specialist=p.name,
            iterations=model_calls,
            cost_usd=round(cost_usd, 6),
        )
        return SpecialistResult(
            output=None,
            tokens_in=tokens_in_total,
            tokens_out=tokens_out_total,
            cost_usd=cost_usd,
            model_calls=model_calls,
            duration_ms=duration_ms,
            error=f"{p.name}_exception: {str(exc)[:300]}",
            status="error",
        )

    duration_ms = int((time.perf_counter() - started) * 1000)
    return SpecialistResult(
        output=None,
        tokens_in=tokens_in_total,
        tokens_out=tokens_out_total,
        cost_usd=cost_usd,
        model_calls=model_calls,
        duration_ms=duration_ms,
        error=(
            f"{p.name}_max_iterations_exceeded ({p.max_iterations}); "
            f"last assistant text: {last_text[:300]}"
        ),
        status="aborted_cap",
    )


def _input_too_large(
    name: str,
    started: float,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    model_calls: int,
    est_in_tokens: int,
    est_input_cost: float,
    threshold: float,
    max_cost_usd: float,
) -> SpecialistResult:
    """Pre-flight gate: input-only cost would exceed 60% of the cap. Abort cleanly
    so the operator sees a structured failure mode rather than a mid-stream cap
    hit (which leaves a partial-cost row that's harder to interpret)."""
    duration_ms = int((time.perf_counter() - started) * 1000)
    log.warning(
        "specialist_input_too_large",
        specialist=name,
        est_input_tokens=est_in_tokens,
        est_input_cost_usd=round(est_input_cost, 6),
        threshold_usd=round(threshold, 6),
        max_cost_usd=round(max_cost_usd, 6),
    )
    return SpecialistResult(
        output=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        model_calls=model_calls,
        duration_ms=duration_ms,
        error=(
            f"input_too_large: estimated input cost ${est_input_cost:.4f} for "
            f"~{est_in_tokens} tokens would exceed {INPUT_COST_CAP_FRACTION * 100:.0f}% of "
            f"the ${max_cost_usd:.2f} cap (threshold ${threshold:.4f}). "
            "Reduce data volume in tools (cap article counts, request fewer periods/series, "
            "shorten filing sections)."
        ),
        status="input_too_large",
    )


def _aborted_cap(
    name: str,
    started: float,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    model_calls: int,
    last_text: str,
    *,
    reason: str,
) -> SpecialistResult:
    duration_ms = int((time.perf_counter() - started) * 1000)
    log.warning(
        "specialist_aborted_cap",
        specialist=name,
        reason=reason,
        iterations=model_calls,
        cost_usd=round(cost_usd, 6),
    )
    return SpecialistResult(
        output=None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        model_calls=model_calls,
        duration_ms=duration_ms,
        error=f"{name}_aborted_cap: {reason}; last text: {last_text[:200]}",
        status="aborted_cap",
    )
