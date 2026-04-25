"""Valuation specialist."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import structlog
from fii_db import SpecialistName
from fii_shared import (
    CitedNumber,
    CompParable,
    SourceRef,
    SourceType,
    ValuationOutput,
)

from fii_agents.model import Model
from fii_agents.prompts import VALUATION_V1, load_active_prompt
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.specialists.llm_loop import LoopParams, run_tool_loop
from fii_agents.tools.shared import (
    DEFAULT_EQUITY_RISK_PREMIUM,
    calculate_beta,
    calculate_wacc,
    get_latest_macro_value,
    run_dcf,
)
from fii_agents.tools.valuation import TOOLS, ValuationToolContext, dispatch

log = structlog.get_logger(__name__)


class ValuationSpecialist:
    name = "valuation"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        if model.is_fake:
            return await self._run_fake(ctx)
        return await self._run_real(ctx, model)

    async def _run_fake(self, ctx: SpecialistContext) -> SpecialistResult:
        started = time.perf_counter()

        # Fetch real inputs via the tools so the output reflects actual DB state.
        rf = get_latest_macro_value(ctx.factory, "DGS10")
        rf_ratio = (rf.get("value") or 4.0) / 100.0  # FRED DGS10 is in percent
        beta_info = calculate_beta(ctx.factory, ctx.symbol)
        beta = float(beta_info.get("beta") or 1.0)
        wacc_info = calculate_wacc(
            risk_free_rate=rf_ratio, beta=beta, equity_risk_premium=DEFAULT_EQUITY_RISK_PREMIUM
        )
        wacc = float(wacc_info["wacc"])

        # Modest defaults — real LLM produces grounded ones.
        base_revenue = 100_000_000_000.0
        shares = 16_000_000_000.0
        net_debt = 0.0
        scenarios = {
            "bear": run_dcf(
                base_revenue=base_revenue,
                revenue_growth=[0.02, 0.02, 0.02, 0.02, 0.02],
                operating_margin=0.25,
                tax_rate=0.21,
                capex_pct_of_revenue=0.04,
                da_pct_of_revenue=0.04,
                nwc_pct_of_revenue=0.0,
                wacc=wacc + 0.01,
                terminal_growth=0.02,
                shares_outstanding=shares,
                net_debt=net_debt,
            ),
            "base": run_dcf(
                base_revenue=base_revenue,
                revenue_growth=[0.05, 0.05, 0.04, 0.04, 0.04],
                operating_margin=0.30,
                tax_rate=0.21,
                capex_pct_of_revenue=0.035,
                da_pct_of_revenue=0.04,
                nwc_pct_of_revenue=0.0,
                wacc=wacc,
                terminal_growth=0.025,
                shares_outstanding=shares,
                net_debt=net_debt,
            ),
            "bull": run_dcf(
                base_revenue=base_revenue,
                revenue_growth=[0.08, 0.08, 0.07, 0.06, 0.05],
                operating_margin=0.33,
                tax_rate=0.21,
                capex_pct_of_revenue=0.03,
                da_pct_of_revenue=0.04,
                nwc_pct_of_revenue=0.0,
                wacc=wacc - 0.005,
                terminal_growth=0.03,
                shares_outstanding=shares,
                net_debt=net_debt,
            ),
        }

        calc = SourceRef(
            source_type=SourceType.CALCULATED,
            source_id="dcf",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        fred_src = SourceRef(
            source_type=SourceType.FRED_SERIES,
            source_id="DGS10",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        price_src = SourceRef(
            source_type=SourceType.POLYGON_PRICE,
            source_id=f"polygon/aggs/{ctx.symbol}/latest",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )

        def _cn(v: float, unit: str, src: SourceRef) -> CitedNumber:
            return CitedNumber(value=float(v), unit=unit, as_of=date.today(), source=src)

        intrinsic_base = float(scenarios["base"].get("intrinsic_value_per_share") or 100.0)
        # Without a current_price tool, use intrinsic as proxy in fake mode.
        current_price = intrinsic_base * 1.05

        output = ValuationOutput(
            dcf_intrinsic_value_bear=_cn(
                scenarios["bear"]["intrinsic_value_per_share"] or 0, "USD", calc
            ),
            dcf_intrinsic_value_base=_cn(intrinsic_base, "USD", calc),
            dcf_intrinsic_value_bull=_cn(
                scenarios["bull"]["intrinsic_value_per_share"] or 0, "USD", calc
            ),
            current_price=_cn(current_price, "USD", price_src),
            margin_of_safety_pct=_cn(
                (intrinsic_base - current_price) / intrinsic_base, "ratio", calc
            ),
            wacc_used=_cn(wacc, "ratio", fred_src),
            terminal_growth_used=_cn(0.025, "ratio", calc),
            revenue_growth_assumption=_cn(0.05, "ratio", calc),
            comparable_multiples=[CompParable(peer_symbol="MSFT", ev_ebitda=22.0, pe_ratio=33.0)],
            reverse_dcf_implied_growth=_cn(0.05, "ratio", calc),
            sensitivity_table={
                "wacc_plus_1pct": intrinsic_base * 0.92,
                "terminal_plus_1pct": intrinsic_base * 1.05,
            },
            qualitative_summary=(
                f"STUB valuation for {ctx.symbol}. WACC was computed from the latest DGS10 "
                "and a 3-year SPY beta. Bear/base/bull DCFs use a five-year revenue ramp "
                "with progressively higher growth and margins. Set ANTHROPIC_API_KEY for "
                "real LLM-driven assumption setting and reverse DCF."
            ),
            confidence="low",
        )
        return SpecialistResult(
            output=output, duration_ms=int((time.perf_counter() - started) * 1000)
        )

    async def _run_real(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        prompt = load_active_prompt(ctx.factory, SpecialistName.VALUATION) or VALUATION_V1
        tool_ctx = ValuationToolContext(factory=ctx.factory)

        async def _dispatch(name: str, input_: dict):
            return await dispatch(name, input_, tool_ctx)

        return await run_tool_loop(
            model,
            LoopParams(
                name=self.name,
                system_prompt=prompt.text,
                user_message=(
                    f"Value {ctx.symbol}. Pull ratios + balance sheet, fetch the risk-free rate and "
                    "beta, calculate WACC, and run bear/base/bull DCFs plus a sensitivity table and a "
                    "reverse DCF. Produce a ValuationOutput as valid JSON."
                ),
                tools=TOOLS,
                dispatch=_dispatch,
                output_schema=ValuationOutput,
            ),
        )
