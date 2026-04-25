"""Insider Flow specialist."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import structlog
from fii_db import SpecialistName
from fii_shared import CitedNumber, InsiderFlowOutput, SourceRef, SourceType

from fii_agents.model import Model
from fii_agents.prompts import INSIDER_V1, load_active_prompt
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.specialists.llm_loop import LoopParams, run_tool_loop
from fii_agents.tools.insider import TOOLS, InsiderToolContext, dispatch

log = structlog.get_logger(__name__)


class InsiderFlowSpecialist:
    name = "insider_flow"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        if model.is_fake:
            return await self._run_fake(ctx)
        return await self._run_real(ctx, model)

    async def _run_fake(self, ctx: SpecialistContext) -> SpecialistResult:
        started = time.perf_counter()
        tool_ctx = InsiderToolContext(factory=ctx.factory)
        f4 = await dispatch(
            "get_form4_transactions", {"symbol": ctx.symbol, "since_days": 180}, tool_ctx
        )
        cluster = await dispatch(
            "detect_cluster_activity", {"symbol": ctx.symbol, "window_days": 30}, tool_ctx
        )

        sec_src = SourceRef(
            source_type=SourceType.SEC_FILING,
            source_id=f"form4/{ctx.symbol}/aggregate",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        cited_zero = CitedNumber(
            value=float(f4.get("net_dollars_trailing_90d") or 0.0),
            unit="USD",
            as_of=date.today(),
            source=sec_src,
        )
        output = InsiderFlowOutput(
            net_insider_dollars_90d=cited_zero,
            cluster_buying=bool(cluster.get("cluster_buying", False)),
            cluster_selling=bool(cluster.get("cluster_selling", False)),
            top_insider_moves=[],
            institutional_net_change_qoq=CitedNumber(
                value=0.0,
                unit="ratio",
                as_of=date.today(),
                source=sec_src,
            ),
            activist_presence=[],
            qualitative_summary=(
                f"STUB insider output for {ctx.symbol}. {f4.get('count', 0)} Form-4 "
                f"transactions in trailing 180 days; cluster buying={cluster.get('cluster_buying')}, "
                f"cluster selling={cluster.get('cluster_selling')}. Set ANTHROPIC_API_KEY "
                "for real interpretation including programmatic-vs-discretionary classification."
            ),
            confidence="low",
        )
        return SpecialistResult(
            output=output, duration_ms=int((time.perf_counter() - started) * 1000)
        )

    async def _run_real(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        prompt = load_active_prompt(ctx.factory, SpecialistName.INSIDER) or INSIDER_V1
        tool_ctx = InsiderToolContext(factory=ctx.factory)

        async def _dispatch(name: str, input_: dict):
            return await dispatch(name, input_, tool_ctx)

        return await run_tool_loop(
            model,
            LoopParams(
                name=self.name,
                system_prompt=prompt.text,
                user_message=(
                    f"Analyze insider flow for {ctx.symbol}. Distinguish programmatic from "
                    "discretionary activity. Produce an InsiderFlowOutput as valid JSON."
                ),
                tools=TOOLS,
                dispatch=_dispatch,
                output_schema=InsiderFlowOutput,
            ),
        )
