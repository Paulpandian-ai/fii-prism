"""Moat specialist."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import structlog
from fii_db import SpecialistName
from fii_shared import (
    CitedClaim,
    FiveForces,
    MoatOutput,
    SourceRef,
    SourceType,
)

from fii_agents.model import Model
from fii_agents.prompts import MOAT_V1, load_active_prompt
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.specialists.llm_loop import LoopParams, run_tool_loop
from fii_agents.tools.moat import TOOLS, MoatToolContext, dispatch

log = structlog.get_logger(__name__)


class MoatSpecialist:
    name = "moat"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        if model.is_fake:
            return await self._run_fake(ctx)
        return await self._run_real(ctx, model)

    async def _run_fake(self, ctx: SpecialistContext) -> SpecialistResult:
        started = time.perf_counter()
        sec = SourceRef(
            source_type=SourceType.SEC_FILING,
            source_id="stub/10-K",
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        output = MoatOutput(
            moat_width="narrow",
            moat_trend="stable",
            moat_types_present=["brand"],
            evidence_for=[],
            evidence_against=[
                CitedClaim(
                    claim="(stub) No real LLM analysis; assess durability with key set.",
                    sources=[sec],
                    confidence="low",
                )
            ],
            five_forces_summary=FiveForces(
                threat_new_entrants="medium",
                bargaining_buyers="medium",
                bargaining_suppliers="medium",
                threat_substitutes="medium",
                competitive_rivalry="medium",
            ),
            qualitative_summary=(
                f"STUB moat output for {ctx.symbol}. Set ANTHROPIC_API_KEY for real "
                "adversarial-frame analysis of competitive durability."
            ),
            confidence="low",
        )
        return SpecialistResult(
            output=output, duration_ms=int((time.perf_counter() - started) * 1000)
        )

    async def _run_real(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        prompt = load_active_prompt(ctx.factory, SpecialistName.MOAT) or MOAT_V1
        tool_ctx = MoatToolContext(factory=ctx.factory, embedder=ctx.embedder)

        async def _dispatch(name: str, input_: dict):
            return await dispatch(name, input_, tool_ctx)

        return await run_tool_loop(
            model,
            LoopParams(
                name=self.name,
                system_prompt=prompt.text,
                user_message=(
                    f"Assess the competitive moat for {ctx.symbol}. Follow the adversarial frame: "
                    "look for erosion evidence FIRST. Produce a MoatOutput as valid JSON."
                ),
                tools=TOOLS,
                dispatch=_dispatch,
                output_schema=MoatOutput,
            ),
        )
