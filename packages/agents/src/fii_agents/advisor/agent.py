"""Advisor turn loop — Anthropic tool-use against the advisor tools.

`run_advisor_turn` does the full loop synchronously and returns the assembled
result. `stream_advisor_turn` yields StreamFrame events suitable for SSE: text
deltas, tool_use_started, tool_result, message_complete.

Fake-mode fallback: when no API key is present, we return a deterministic stub
response so the chat UI works end-to-end without an Anthropic key.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from fii_agents.advisor.tools import (
    ADVISOR_TOOLS,
    AdvisorToolContext,
    referenced_analysis_ids_from_tool_calls,
)
from fii_agents.advisor.tools import (
    dispatch as dispatch_tool,
)
from fii_agents.budget import estimate_cost_usd
from fii_agents.model import MODEL_SONNET, Model

log = structlog.get_logger(__name__)


SYSTEM_PROMPT = """You are FII-PRISM's Wealth Advisor — a specialist that helps the user reason \
about their actual portfolio. You answer using the user's REAL holdings, real cost basis, \
and real prior analyses (with analysis_id citations).

Hard rules — every response must follow them:

1. Always start by calling `get_my_portfolio` for any question about a position the user holds.
2. Before answering "should I buy/sell/hold X", call `get_latest_analysis` for X. If \
`age_days > 7` or `status == "not_found"`, call `run_quick_analysis` first, then proceed.
3. Never recommend an action without specifying: position size (shares or % of \
portfolio), entry price (or "current"), and a stop level (price or % drawdown).
4. Cite the analysis_id of every analysis you reference — e.g. "the latest deep-dive \
(analysis_id=abc-123) puts XOM at fii_score=6.4". The UI will turn these into links.
5. For sell decisions, always call `calculate_tax_loss_harvest` to surface tax \
consequences. For buy decisions, call `calculate_portfolio_impact` to surface \
concentration breaches.
6. Educational framing always. End every response with EXACTLY this line on its own:
   For educational purposes only. Not investment advice.

Style: tight paragraphs. No marketing speak. No bullet-explosion. Lead with the \
recommendation, then the evidence."""


# --- Stream frames ------------------------------------------------------------------------


@dataclass
class StreamFrame:
    """One SSE frame. The route serializes this as JSON on the wire."""

    kind: str  # "text" | "tool_use_started" | "tool_result" | "message_complete" | "error"
    text: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    referenced_analysis_ids: list[str] | None = None
    final_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        for k in (
            "text",
            "tool_name",
            "tool_input",
            "tool_result",
            "cost_usd",
            "tokens_in",
            "tokens_out",
            "referenced_analysis_ids",
            "final_text",
        ):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


# --- Turn result --------------------------------------------------------------------------


@dataclass
class AdvisorTurn:
    """Aggregated result of one assistant turn."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    referenced_analysis_ids: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model_used: str = MODEL_SONNET


# --- Public API ---------------------------------------------------------------------------


# Type for a per-turn tool_use → tool_result hook so the API can stream live frames.
ToolHook = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[None]]


async def run_advisor_turn(
    *,
    model: Model,
    history: list[dict[str, Any]],
    user_message: str,
    tool_ctx: AdvisorToolContext,
    max_iterations: int = 8,
    tool_hook: ToolHook | None = None,
) -> AdvisorTurn:
    """One assistant turn (one user message → one assistant final text). The loop
    handles all tool round-trips internally."""
    if model.is_fake:
        return _fake_turn(user_message)

    # Build the messages array: prior history (user/assistant + tool blocks) + this message.
    messages: list[dict[str, Any]] = [
        *history,
        {"role": "user", "content": [{"type": "text", "text": user_message}]},
    ]
    tool_calls: list[dict[str, Any]] = []
    tokens_in = 0
    tokens_out = 0
    cost = 0.0
    last_text = ""

    for _ in range(max_iterations):
        call = await model.respond(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=ADVISOR_TOOLS,
        )
        tokens_in += call.tokens_in
        tokens_out += call.tokens_out
        cost += estimate_cost_usd(call.model, call.tokens_in, call.tokens_out)
        if call.text:
            last_text = call.text

        if call.stop_reason == "tool_use" and call.tool_calls:
            assistant_blocks: list[dict[str, Any]] = []
            if call.text:
                assistant_blocks.append({"type": "text", "text": call.text})
            for tc in call.tool_calls:
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    }
                )
            messages.append({"role": "assistant", "content": assistant_blocks})

            results_block: dict[str, Any] = {"role": "user", "content": []}
            for tc in call.tool_calls:
                result = await dispatch_tool(tc["name"], tc["input"], tool_ctx)
                tool_calls.append(
                    {"id": tc["id"], "name": tc["name"], "input": tc["input"], "result": result}
                )
                if tool_hook is not None:
                    await tool_hook(tc["name"], tc["input"], result)
                results_block["content"].append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc["id"],
                        "content": json.dumps(result, default=str),
                    }
                )
            messages.append(results_block)
            continue

        # Terminal: assistant text only.
        return AdvisorTurn(
            text=last_text,
            tool_calls=tool_calls,
            referenced_analysis_ids=referenced_analysis_ids_from_tool_calls(tool_calls),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            model_used=call.model,
        )

    return AdvisorTurn(
        text=last_text or "I wasn't able to finish reasoning in the iteration budget. Try again.",
        tool_calls=tool_calls,
        referenced_analysis_ids=referenced_analysis_ids_from_tool_calls(tool_calls),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        model_used=model.model_id,
    )


async def stream_advisor_turn(
    *,
    model: Model,
    history: list[dict[str, Any]],
    user_message: str,
    tool_ctx: AdvisorToolContext,
    max_iterations: int = 8,
) -> AsyncIterator[StreamFrame]:
    """Yield SSE frames as the turn progresses. The actual model call here is non-streaming
    (Anthropic's tool-use streaming is more complex than we need for v1); we surface
    one frame per tool round-trip and one final text frame."""

    async def _hook(name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        # Capture and forward.
        await _frame_queue.put(
            StreamFrame(kind="tool_use_started", tool_name=name, tool_input=args)
        )
        await _frame_queue.put(StreamFrame(kind="tool_result", tool_name=name, tool_result=result))

    import asyncio

    _frame_queue: asyncio.Queue[StreamFrame | None] = asyncio.Queue()

    async def _runner() -> None:
        try:
            turn = await run_advisor_turn(
                model=model,
                history=history,
                user_message=user_message,
                tool_ctx=tool_ctx,
                max_iterations=max_iterations,
                tool_hook=_hook,
            )
            await _frame_queue.put(
                StreamFrame(
                    kind="message_complete",
                    final_text=turn.text,
                    cost_usd=turn.cost_usd,
                    tokens_in=turn.tokens_in,
                    tokens_out=turn.tokens_out,
                    referenced_analysis_ids=turn.referenced_analysis_ids,
                )
            )
        except Exception as exc:
            log.exception("advisor_stream_failed")
            await _frame_queue.put(StreamFrame(kind="error", text=str(exc)))
        finally:
            await _frame_queue.put(None)

    task = asyncio.create_task(_runner())
    try:
        while True:
            frame = await _frame_queue.get()
            if frame is None:
                break
            yield frame
    finally:
        if not task.done():
            task.cancel()


# --- Fake-mode fallback -------------------------------------------------------------------


def _fake_turn(user_message: str) -> AdvisorTurn:
    """Deterministic stub used when ANTHROPIC_API_KEY is unset."""
    text = (
        f"[fake-mode advisor] You asked: {user_message[:200]}.\n\n"
        "In fake mode I don't make real model calls. The full advisor loop runs against "
        "Sonnet 4.6 with prompt caching when ANTHROPIC_API_KEY is set; it always cites "
        "analysis_ids and ends with the educational disclaimer.\n\n"
        "For educational purposes only. Not investment advice."
    )
    return AdvisorTurn(text=text, model_used="fake")
