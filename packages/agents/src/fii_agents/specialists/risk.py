"""Risk specialist (used for both preliminary and final passes).

Both passes use the same class. The orchestrator sets ctx.prior to indicate which
pass: empty prior == preliminary; populated with bull/bear == final.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import structlog
from fii_db import SpecialistName
from fii_shared import (
    CitedNumber,
    DfastScenario,
    RiskOutput,
    SourceRef,
    SourceType,
)

from fii_agents.model import Model
from fii_agents.prompts import RISK_V1, load_active_prompt
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.specialists.llm_loop import LoopParams, run_tool_loop
from fii_agents.tools.risk import DFAST_SCENARIOS, TOOLS, RiskToolContext, dispatch

log = structlog.get_logger(__name__)


class RiskSpecialist:
    name = "risk"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        if model.is_fake:
            return await self._run_fake(ctx)
        return await self._run_real(ctx, model)

    async def _run_fake(self, ctx: SpecialistContext) -> SpecialistResult:
        started = time.perf_counter()
        tool_ctx = RiskToolContext(factory=ctx.factory)
        dfast = await dispatch(
            "calculate_dfast_scenarios",
            {"symbol": ctx.symbol, "position_value_usd": 10_000.0},
            tool_ctx,
        )
        adv = await dispatch("get_avg_daily_volume", {"symbol": ctx.symbol}, tool_ctx)
        corr = await dispatch("get_correlation_to_benchmark", {"symbol": ctx.symbol}, tool_ctx)
        mdd = await dispatch("get_max_drawdown_historical", {"symbol": ctx.symbol}, tool_ctx)

        ps = SourceRef(
            source_type=SourceType.POLYGON_PRICE,
            source_id=f"polygon/aggs/{ctx.symbol}/latest",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        calc = SourceRef(
            source_type=SourceType.CALCULATED,
            source_id="risk/derived",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        scenarios = {name: DfastScenario(**s) for name, s in (dfast.get("scenarios") or {}).items()}
        if not scenarios:
            # No price history — synthesize a placeholder set.
            scenarios = {
                name: DfastScenario(
                    name=name,
                    assumed_move_pct=move,
                    position_value_start_usd=10_000.0,
                    position_value_after_usd=10_000.0 * (1 + move),
                    drawdown_usd=10_000.0 * move,
                    notes=None,
                )
                for name, move in DFAST_SCENARIOS.items()
            }

        liquid = bool(adv.get("avg_daily_dollar_volume") and adv["avg_daily_dollar_volume"] > 5e6)
        output = RiskOutput(
            position_size_rec_pct=3.0,
            hard_stop_level=CitedNumber(
                value=0.0,
                unit="USD",
                as_of=date.today(),
                source=ps,
            ),
            max_drawdown_historical=CitedNumber(
                value=float(mdd.get("max_drawdown") or -0.30),
                unit="ratio",
                as_of=date.today(),
                source=calc,
            ),
            correlation_to_portfolio=float(corr.get("correlation") or 0.50),
            liquidity_adequate=liquid,
            concentration_warnings=[],
            dfast_scenarios=scenarios,
            go_no_go="approve_with_conditions",
            conditions=["(stub) Set ANTHROPIC_API_KEY for real go/no-go reasoning."],
            qualitative_summary=(
                f"STUB risk output for {ctx.symbol}. ADV liquid={liquid}, "
                f"max_drawdown={mdd.get('max_drawdown')}, corr={corr.get('correlation')}."
            ),
            confidence="low",
        )
        return SpecialistResult(
            output=output, duration_ms=int((time.perf_counter() - started) * 1000)
        )

    async def _run_real(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        prompt = load_active_prompt(ctx.factory, SpecialistName.RISK) or RISK_V1
        tool_ctx = RiskToolContext(factory=ctx.factory)

        async def _dispatch(name: str, input_: dict):
            return await dispatch(name, input_, tool_ctx)

        is_final = bool(ctx.prior.get("bull") or ctx.prior.get("bear"))
        if is_final:
            user_msg = (
                f"FINAL pass for {ctx.symbol}. The bull and bear cases are in your context. "
                "Identify deal-breakers the other specialists missed. Update go_no_go and "
                "conditions accordingly. Produce a RiskOutput as valid JSON."
            )
        else:
            user_msg = (
                f"PRELIMINARY risk pass for {ctx.symbol}. Pull DFAST scenarios, ADV, correlation, "
                "drawdown history. Produce a RiskOutput as valid JSON."
            )

        return await run_tool_loop(
            model,
            LoopParams(
                name=self.name,
                system_prompt=prompt.text,
                user_message=user_msg,
                tools=TOOLS,
                dispatch=_dispatch,
                output_schema=RiskOutput,
            ),
        )
