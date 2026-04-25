"""Technical specialist."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import structlog
from fii_db import SpecialistName
from fii_shared import CitedNumber, SourceRef, SourceType, TechnicalOutput

from fii_agents.model import Model
from fii_agents.prompts import TECHNICAL_V1, load_active_prompt
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.specialists.llm_loop import LoopParams, run_tool_loop
from fii_agents.tools.technical import TOOLS, TechnicalToolContext, dispatch

log = structlog.get_logger(__name__)


class TechnicalSpecialist:
    name = "technical"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        if model.is_fake:
            return await self._run_fake(ctx)
        return await self._run_real(ctx, model)

    async def _run_fake(self, ctx: SpecialistContext) -> SpecialistResult:
        started = time.perf_counter()
        tool_ctx = TechnicalToolContext(factory=ctx.factory)
        ind = await dispatch("get_indicators", {"symbol": ctx.symbol}, tool_ctx)

        if "error" in ind:
            # No price history yet — emit a permissive but valid placeholder.
            ind = {
                "rsi_14": 50.0,
                "macd": {"cross": "no_signal"},
                "adx_14": 20.0,
                "atr_14": 1.0,
                "realized_vol_30d": 0.20,
                "support_levels": [0.0],
                "resistance_levels": [0.0],
                "trend_short": "sideways",
                "trend_medium": "sideways",
                "trend_long": "sideways",
                "last_close": 0.0,
            }

        ps = SourceRef(
            source_type=SourceType.POLYGON_PRICE,
            source_id=f"polygon/aggs/{ctx.symbol}/latest",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )

        def _cn(v: float) -> CitedNumber:
            return CitedNumber(value=float(v), unit="USD", as_of=date.today(), source=ps)

        last = float(ind.get("last_close") or 0.0) or 100.0
        atr = float(ind.get("atr_14") or 1.0) or 1.0
        supports = ind.get("support_levels") or [last - 2 * atr]
        resistances = ind.get("resistance_levels") or [last + 2 * atr]
        macd_v = ind.get("macd") or {}
        macd_cross = macd_v.get("cross", "no_signal")

        output = TechnicalOutput(
            trend_short=ind.get("trend_short") or "sideways",
            trend_medium=ind.get("trend_medium") or "sideways",
            trend_long=ind.get("trend_long") or "sideways",
            rsi_14=float(ind.get("rsi_14") or 50.0),
            macd_signal=macd_cross,
            adx=float(ind.get("adx_14") or 20.0),
            realized_vol_30d=float(ind.get("realized_vol_30d") or 0.20),
            atr_14=atr,
            support_levels=[float(x) for x in supports],
            resistance_levels=[float(x) for x in resistances],
            suggested_entry=_cn(last),
            suggested_stop=_cn(supports[0] if supports else last - 2 * atr),
            suggested_target=_cn(resistances[0] if resistances else last + 2 * atr),
            regime="trending" if (ind.get("adx_14") or 0) > 25 else "mean_reverting",
            signal="neutral",
            signal_strength="moderate",
            qualitative_summary=(
                f"STUB technical output for {ctx.symbol}. Indicators were computed deterministically; "
                "set ANTHROPIC_API_KEY for the LLM interpretation layer."
            ),
            confidence="medium",
        )
        return SpecialistResult(
            output=output, duration_ms=int((time.perf_counter() - started) * 1000)
        )

    async def _run_real(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        prompt = load_active_prompt(ctx.factory, SpecialistName.TECHNICAL) or TECHNICAL_V1
        tool_ctx = TechnicalToolContext(factory=ctx.factory)

        async def _dispatch(name: str, input_: dict):
            return await dispatch(name, input_, tool_ctx)

        return await run_tool_loop(
            model,
            LoopParams(
                name=self.name,
                system_prompt=prompt.text,
                user_message=(
                    f"Read the indicator dict for {ctx.symbol} via get_indicators and produce a "
                    "TechnicalOutput. Do not compute any indicator yourself."
                ),
                tools=TOOLS,
                dispatch=_dispatch,
                output_schema=TechnicalOutput,
            ),
        )
