"""Cost budget enforcement for a single analysis run.

Default hard cap is $1.00 per analysis (configurable via FII_ANALYSIS_BUDGET_USD).
Before each specialist runs, we check the running total; if we're already over cap
the specialist is skipped (returns a stub with a budget_exceeded error). This lets
synthesis proceed with partial data rather than crashing halfway through.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_HARD_CAP_USD = 1.00


@dataclass(frozen=True)
class Budget:
    hard_cap_usd: float = DEFAULT_HARD_CAP_USD

    @classmethod
    def from_env(cls) -> Budget:
        raw = os.environ.get("FII_ANALYSIS_BUDGET_USD")
        if raw:
            try:
                return cls(hard_cap_usd=float(raw))
            except ValueError:
                pass
        return cls()

    def is_exceeded(self, spent_usd: float) -> bool:
        return spent_usd >= self.hard_cap_usd

    def remaining(self, spent_usd: float) -> float:
        return max(0.0, self.hard_cap_usd - spent_usd)


# --- Anthropic pricing (us-east-1 / direct API, 2026-Q1) ---------------------------------
# Tuned to match Section 0 routing: Haiku 4.5 for retrieval, Sonnet 4.6 for reasoning,
# Opus 4.7 only for final synthesis if budget allows.

MODEL_PRICING_PER_MTOKEN: dict[str, tuple[float, float]] = {
    # (input_usd_per_mtoken, output_usd_per_mtoken)
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
}


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pricing = MODEL_PRICING_PER_MTOKEN.get(model)
    if not pricing:
        # Unknown model — assume Sonnet rates so we err on the side of caution.
        pricing = MODEL_PRICING_PER_MTOKEN["claude-sonnet-4-6"]
    in_rate, out_rate = pricing
    return (tokens_in / 1_000_000) * in_rate + (tokens_out / 1_000_000) * out_rate


# --- Pre-flight token estimation -----------------------------------------------------------

# Rough character→token ratio. Anthropic's tokenizer is BPE so 1 token ≈ 3.5-4
# characters of English text; we use 4 to err on the side of *under*-estimating
# tokens (i.e., we're pessimistic about cost — a real run that fits under 60%
# of the cap by this estimate has comfortable headroom).
_APPROX_CHARS_PER_TOKEN = 4


def _block_chars(block: object) -> int:
    if isinstance(block, str):
        return len(block)
    if not isinstance(block, dict):
        return 0
    if "text" in block:
        return len(block.get("text") or "")
    if "content" in block:
        c = block.get("content")
        if isinstance(c, str):
            return len(c)
        if isinstance(c, list):
            return sum(_block_chars(b) for b in c)
    if "input" in block:
        # tool_use input is JSON-serialized inside the API request.
        import json as _json

        return len(_json.dumps(block.get("input") or {}, default=str))
    return 0


def estimate_input_tokens(messages: list[dict], system: str | None = None) -> int:
    """Crude pre-flight estimate of how many input tokens the next API call
    will consume. Walks the assistant/user content blocks the same way the SDK
    serializes them to wire format (text + tool_use input + tool_result content).
    """
    total_chars = len(system or "")
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                total_chars += _block_chars(block)
    return total_chars // _APPROX_CHARS_PER_TOKEN
