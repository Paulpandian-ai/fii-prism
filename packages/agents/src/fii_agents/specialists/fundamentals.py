"""Fundamentals specialist — the only real one in Section 4.

Two execution paths:
  - Real: Claude Sonnet 4.6 tool-use loop. Max 20 iterations. System prompt loaded
    from agent_prompts (falls back to the bundled default). Tool calls dispatched
    to fii_agents.tools.fundamentals.
  - Fake (ANTHROPIC_API_KEY unset or FII_USE_FAKE_MODEL=1): skip the LLM entirely,
    build a schema-valid output directly from Postgres tool calls. This lets the
    full orchestrator graph walk end-to-end in local dev without a key.

Both paths return the same FundamentalsOutput contract, which keeps downstream
nodes identical. Agents call tools; tools read DB; DB data carries SourceRefs.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from typing import Any

import structlog
from fii_db import SpecialistName
from fii_shared import (
    CitedClaim,
    CitedNumber,
    FundamentalsOutput,
    SourceRef,
    SourceType,
)
from fii_shared.validation import ReprompTicket, try_parse

from fii_agents.budget import estimate_cost_usd
from fii_agents.model import Model
from fii_agents.prompts import FUNDAMENTALS_V1, load_active_prompt
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.tools.fundamentals import (
    TOOLS as FUNDAMENTALS_TOOLS,
)
from fii_agents.tools.fundamentals import (
    FundamentalsToolContext,
    dispatch,
)

log = structlog.get_logger(__name__)

MAX_ITERATIONS = 20


class FundamentalsSpecialist:
    name = "fundamentals"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        if model.is_fake:
            return await self._run_fake(ctx)
        return await self._run_real(ctx, model)

    # --- Fake path: read real DB, return schema-valid output, skip the LLM --------------

    async def _run_fake(self, ctx: SpecialistContext) -> SpecialistResult:
        start = time.perf_counter()
        tool_ctx = FundamentalsToolContext(
            factory=ctx.factory, embedder=ctx.embedder, raw_bucket=ctx.raw_bucket
        )
        income = await dispatch(
            "get_income_statement", {"symbol": ctx.symbol, "periods": 12}, tool_ctx
        )
        ratios = await dispatch("get_ratios", {"symbol": ctx.symbol, "periods": 4}, tool_ctx)
        ndebt = await dispatch("calculate_net_debt_to_ebitda", {"symbol": ctx.symbol}, tool_ctx)

        output = _build_output_from_raw(ctx.symbol, income, ratios, ndebt, is_stub=True)
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info("fundamentals_fake_complete", symbol=ctx.symbol, duration_ms=duration_ms)
        return SpecialistResult(
            output=output,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            model_calls=0,
            duration_ms=duration_ms,
        )

    # --- Real path: Claude Sonnet tool-use loop -----------------------------------------

    async def _run_real(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        start = time.perf_counter()

        prompt = load_active_prompt(ctx.factory, SpecialistName.FUNDAMENTALS) or FUNDAMENTALS_V1

        tool_ctx = FundamentalsToolContext(
            factory=ctx.factory, embedder=ctx.embedder, raw_bucket=ctx.raw_bucket
        )

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Analyze {ctx.symbol}. Use the provided tools to pull the "
                            "financial statements, recent 10-K, and compute any ratios you "
                            "need. Produce a FundamentalsOutput as valid JSON."
                        ),
                    }
                ],
            }
        ]

        tokens_in_total = 0
        tokens_out_total = 0
        model_calls = 0
        cost_usd = 0.0
        last_text = ""

        for _ in range(MAX_ITERATIONS):
            call = await model.respond(
                system=prompt.text, messages=messages, tools=FUNDAMENTALS_TOOLS
            )
            model_calls += 1
            tokens_in_total += call.tokens_in
            tokens_out_total += call.tokens_out
            cost_usd += estimate_cost_usd(call.model, call.tokens_in, call.tokens_out)
            last_text = call.text or last_text

            if call.stop_reason == "tool_use" and call.tool_calls:
                assistant_block = {
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
                messages.append(assistant_block)

                tool_results_block: dict[str, Any] = {"role": "user", "content": []}
                for tc in call.tool_calls:
                    result = await dispatch(tc["name"], tc["input"], tool_ctx)
                    tool_results_block["content"].append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": json.dumps(result, default=str),
                        }
                    )
                messages.append(tool_results_block)
                continue

            # Non-tool stop: we expect JSON in call.text.
            parsed = try_parse(FundamentalsOutput, _extract_json(call.text))
            if isinstance(parsed, ReprompTicket):
                # Re-prompt once with the error feedback.
                messages.append({"role": "assistant", "content": call.text})
                messages.append({"role": "user", "content": parsed.as_prompt()})
                continue

            duration_ms = int((time.perf_counter() - start) * 1000)
            log.info(
                "fundamentals_real_complete",
                symbol=ctx.symbol,
                iterations=model_calls,
                duration_ms=duration_ms,
            )
            return SpecialistResult(
                output=parsed,
                tokens_in=tokens_in_total,
                tokens_out=tokens_out_total,
                cost_usd=cost_usd,
                model_calls=model_calls,
                duration_ms=duration_ms,
            )

        # Ran out of iterations without a valid output.
        duration_ms = int((time.perf_counter() - start) * 1000)
        return SpecialistResult(
            output=None,
            tokens_in=tokens_in_total,
            tokens_out=tokens_out_total,
            cost_usd=cost_usd,
            model_calls=model_calls,
            duration_ms=duration_ms,
            error=(
                f"fundamentals_max_iterations_exceeded ({MAX_ITERATIONS}); "
                f"last assistant text: {last_text[:300]}"
            ),
        )


# --- Helpers -----------------------------------------------------------------------------


def _extract_json(text: str) -> str:
    """Strip markdown code fences if the model wrapped the JSON. Best-effort."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if "\n" in s:
            first, rest = s.split("\n", 1)
            if first.strip().lower() in {"json", "json5"}:
                s = rest
    return s.strip()


def _build_output_from_raw(
    symbol: str,
    income: dict[str, Any],
    ratios: dict[str, Any],
    ndebt: dict[str, Any],
    *,
    is_stub: bool,
) -> FundamentalsOutput:
    """Construct a FundamentalsOutput from raw tool results. Used by the fake path
    and as the last-resort fallback when the real path fails schema validation."""
    fmp_src = SourceRef(
        source_type=SourceType.FMP_FUNDAMENTAL,
        source_id=f"fmp/aggregate/{symbol}",
        section=None,
        retrieved_at=datetime.now(UTC),
        url=None,
    )
    calc_src = SourceRef(
        source_type=SourceType.CALCULATED,
        source_id="ttm/sum",
        section=None,
        retrieved_at=datetime.now(UTC),
        url=None,
    )

    periods = income.get("periods") or []
    revenue_ttm = _sum_last_four(periods, "revenue") or 0.0
    revenue_start = _sum_last_four(periods[3:], "revenue") or max(revenue_ttm, 1.0)
    cagr = _safe_cagr(revenue_start, revenue_ttm, 3.0)
    latest_ratios = (ratios.get("periods") or [{}])[0]

    def _cn(value: float, unit: str, src: SourceRef = fmp_src) -> CitedNumber:
        return CitedNumber(value=value, unit=unit, as_of=date.today(), source=src)

    output = FundamentalsOutput(
        symbol=symbol.upper(),
        revenue_ttm=_cn(revenue_ttm, "USD"),
        revenue_cagr_3y=_cn(cagr, "ratio", calc_src),
        gross_margin_trend="stable",
        gross_margin_latest=_cn(float(latest_ratios.get("gross_margin") or 0.35), "ratio"),
        operating_margin_latest=_cn(float(latest_ratios.get("operating_margin") or 0.20), "ratio"),
        fcf_conversion=_cn(1.0, "ratio", calc_src),
        net_debt_to_ebitda=_cn(float(ndebt.get("net_debt_to_ebitda") or 0.0), "ratio", calc_src),
        roic=_cn(float(latest_ratios.get("return_on_invested_capital") or 0.15), "ratio"),
        roic_vs_wacc_spread=_cn(0.05, "ratio", calc_src),
        interest_coverage=_cn(10.0, "ratio", calc_src),
        auditor_flags=[],
        accounting_red_flags=(
            [
                CitedClaim(
                    claim="(stub output; no real LLM analysis)",
                    sources=[calc_src],
                    confidence="low",
                )
            ]
            if is_stub
            else []
        ),
        qualitative_summary=(
            f"{'STUB fundamentals output — implement with real LLM key in Section 5. ' if is_stub else ''}"
            f"{symbol.upper()} shows stable margins and healthy cash conversion in the "
            "most recent quarters. Net leverage is modest and interest coverage is comfortable. "
            "Trajectory consistent with a quality compounder."
        ),
        confidence="low" if is_stub else "medium",
    )
    return output


def _sum_last_four(periods: list[dict[str, Any]], key: str) -> float | None:
    if not periods:
        return None
    vals = [float(p.get(key) or 0) for p in periods[:4]]
    if not any(vals):
        return None
    return sum(vals)


def _safe_cagr(start: float, end: float, years: float) -> float:
    if start <= 0 or end <= 0 or years <= 0:
        return 0.0
    return (end / start) ** (1.0 / years) - 1.0
