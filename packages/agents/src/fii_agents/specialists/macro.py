"""Macro specialist."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import structlog
from fii_db import SpecialistName
from fii_shared import CitedClaim, MacroOutput, SourceRef, SourceType

from fii_agents.model import Model
from fii_agents.prompts import MACRO_V1, load_active_prompt
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.specialists.llm_loop import LoopParams, run_tool_loop
from fii_agents.tools.macro import TOOLS, MacroToolContext, dispatch
from fii_agents.tools.shared import get_latest_macro_value, get_yield_curve

log = structlog.get_logger(__name__)


class MacroSpecialist:
    name = "macro"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        if model.is_fake:
            return await self._run_fake(ctx)
        return await self._run_real(ctx, model)

    async def _run_fake(self, ctx: SpecialistContext) -> SpecialistResult:
        started = time.perf_counter()
        yc = get_yield_curve(ctx.factory)
        unrate = get_latest_macro_value(ctx.factory, "UNRATE")

        regime = "expansion"
        if yc.get("inverted"):
            regime = "late_cycle"

        unrate_src = SourceRef(
            source_type=SourceType.FRED_SERIES,
            source_id="UNRATE",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        dgs10_src = SourceRef(
            source_type=SourceType.FRED_SERIES,
            source_id="DGS10",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        output = MacroOutput(
            regime=regime,
            regime_evidence=[
                CitedClaim(
                    claim=f"UNRATE latest value: {unrate.get('value')}",
                    sources=[unrate_src],
                    confidence="medium",
                ),
            ],
            rates_trajectory="neutral",
            stock_sector_macro_sensitivity={"rates_10y": -0.3, "vix": -0.5, "oil": 0.0},
            top_risks=[
                CitedClaim(
                    claim="(stub) recession-multiple-compression risk",
                    sources=[dgs10_src],
                    confidence="low",
                )
            ],
            top_tailwinds=[],
            qualitative_summary=(
                f"STUB macro output. Yield curve inverted={yc.get('inverted')}. "
                "Set ANTHROPIC_API_KEY for real regime analysis grounded in 3+ FRED series."
            ),
            confidence="low",
        )
        return SpecialistResult(
            output=output, duration_ms=int((time.perf_counter() - started) * 1000)
        )

    async def _run_real(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        prompt = load_active_prompt(ctx.factory, SpecialistName.MACRO) or MACRO_V1
        tool_ctx = MacroToolContext(factory=ctx.factory)

        async def _dispatch(name: str, input_: dict):
            return await dispatch(name, input_, tool_ctx)

        return await run_tool_loop(
            model,
            LoopParams(
                name=self.name,
                system_prompt=prompt.text,
                user_message=(
                    f"Classify the current macro regime in the context of {ctx.symbol}. Start with "
                    "classify_regime, then ground the regime label in 3+ FRED series. Produce a "
                    "MacroOutput as valid JSON."
                ),
                tools=TOOLS,
                dispatch=_dispatch,
                output_schema=MacroOutput,
            ),
        )
