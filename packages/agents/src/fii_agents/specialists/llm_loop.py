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

from fii_agents.budget import estimate_cost_usd
from fii_agents.model import Model
from fii_agents.specialists.base import SpecialistResult

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

    started = time.perf_counter()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": p.user_message}]}
    ]
    tokens_in_total = 0
    tokens_out_total = 0
    model_calls = 0
    cost_usd = 0.0
    last_text = ""

    for _ in range(p.max_iterations):
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
    )
