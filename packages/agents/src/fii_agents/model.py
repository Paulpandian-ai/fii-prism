"""Model wrapper around the Anthropic Python SDK.

Real mode: uses `anthropic.AsyncAnthropic` with tool-use and prompt caching.
Fake mode (activated when ANTHROPIC_API_KEY is unset or FII_USE_FAKE_MODEL=1):
returns deterministic responses so the graph + streaming + persistence layers can
be exercised end-to-end without an API key. Fundamentals still runs against real
Postgres data in fake mode; it just skips the LLM call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Model IDs — kept in one place so we can swap defaults in one edit.
MODEL_HAIKU = "claude-haiku-4-5"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_OPUS = "claude-opus-4-7"


def is_fake_mode() -> bool:
    if os.environ.get("FII_USE_FAKE_MODEL") == "1":
        return True
    return not os.environ.get("ANTHROPIC_API_KEY")


@dataclass
class ModelCall:
    """Result of one Claude API call. Returned by Model.respond()."""

    text: str
    tool_calls: list[dict[str, Any]]
    stop_reason: str
    tokens_in: int
    tokens_out: int
    model: str

    def primary_tool_call(self) -> dict[str, Any] | None:
        return self.tool_calls[0] if self.tool_calls else None


class Model:
    """Minimal async facade. Callers pass messages + optional tools."""

    def __init__(self, *, model_id: str = MODEL_SONNET, max_tokens: int = 4096) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self._client: Any | None = None
        if not is_fake_mode():
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    @property
    def is_fake(self) -> bool:
        return self._client is None

    async def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> ModelCall:
        """One turn. Returns the assistant's content and any tool_use blocks."""
        if self._client is None:
            raise RuntimeError(
                "Model.respond called in fake mode; callers should short-circuit "
                "by checking is_fake() before hitting the API."
            )

        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        resp = await self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in resp.content:
            kind = getattr(block, "type", None)
            if kind == "text":
                text_parts.append(block.text)
            elif kind == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})

        usage = resp.usage
        return ModelCall(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=str(resp.stop_reason),
            tokens_in=int(usage.input_tokens),
            tokens_out=int(usage.output_tokens),
            model=self.model_id,
        )
