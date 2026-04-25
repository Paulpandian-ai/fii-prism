"""Bull and Bear researchers — debate pair.

Both run with NO tools. They receive the upstream specialist outputs in their user
message and produce a BullBearDebateOutput. Their prompt forbids introducing new
claims; we enforce a runtime check that every CitedClaim's source_id appears in
some upstream specialist output (best-effort string match).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import structlog
from fii_db import SpecialistName
from fii_shared import (
    BullBearDebateOutput,
    CitedClaim,
    SourceRef,
    SourceType,
)
from pydantic import BaseModel

from fii_agents.model import Model
from fii_agents.prompts import BEAR_V1, BULL_V1, load_active_prompt
from fii_agents.specialists.base import SpecialistContext, SpecialistResult
from fii_agents.specialists.llm_loop import LoopParams, run_tool_loop

log = structlog.get_logger(__name__)


def _collect_source_ids(prior: dict) -> set[str]:
    """Walk the upstream specialist outputs (Pydantic models OR plain dicts) and return
    every SourceRef.source_id we find. Bull/Bear claims must reference one of these."""
    out: set[str] = set()

    def _walk(obj):
        if isinstance(obj, BaseModel):
            for v in obj.__dict__.values():
                _walk(v)
        elif isinstance(obj, list | tuple):
            for x in obj:
                _walk(x)
        elif isinstance(obj, dict):
            sid = obj.get("source_id") if "source_id" in obj else None
            if isinstance(sid, str) and sid:
                out.add(sid)
            for v in obj.values():
                _walk(v)
        elif hasattr(obj, "source_id"):
            out.add(str(obj.source_id))

    for spec_output in prior.values():
        _walk(spec_output)
    return out


def claim_coverage_violations(output: BullBearDebateOutput, allowed_sources: set[str]) -> list[str]:
    """Return source_ids cited by the debate output that don't appear in upstream sources."""
    bad: list[str] = []
    for claim in [*output.strongest_evidence, *output.weakest_evidence]:
        for src in claim.sources:
            if src.source_id not in allowed_sources:
                bad.append(src.source_id)
    return bad


class _DebateBase:
    name: str = "debate"
    side: str = "bull"
    prompt_const = BULL_V1

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        if model.is_fake:
            return self._fake(ctx)
        return await self._real(ctx, model)

    def _fake(self, ctx: SpecialistContext) -> SpecialistResult:
        started = time.perf_counter()
        sources = _collect_source_ids(ctx.prior)
        first_source = next(iter(sources)) if sources else "stub/no_upstream_sources"
        src = SourceRef(
            source_type=SourceType.CALCULATED,
            source_id=first_source,
            section=None,
            retrieved_at=datetime.now(UTC),
            url=None,
        )
        case = (
            f"STUB {self.side} case for {ctx.symbol}. Sourced from upstream specialists; "
            "set ANTHROPIC_API_KEY for real debate."
        )
        output = BullBearDebateOutput(
            case=case,
            strongest_evidence=[
                CitedClaim(
                    claim=f"(stub) {self.side} thesis stand-in",
                    sources=[src],
                    confidence="low",
                )
            ],
            weakest_evidence=[],
            what_would_change_my_mind=(
                f"(stub) Replace this with a real {self.side}-case trigger."
            ),
            confidence="low",
        )
        return SpecialistResult(
            output=output, duration_ms=int((time.perf_counter() - started) * 1000)
        )

    async def _real(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        prompt_record = (
            load_active_prompt(
                ctx.factory,
                SpecialistName.BULL if self.side == "bull" else SpecialistName.BEAR,
            )
            or self.prompt_const
        )

        priors_serialized = {
            k: (v.model_dump(mode="json") if hasattr(v, "model_dump") else v)
            for k, v in ctx.prior.items()
        }
        user = (
            f"{self.side.upper()} researcher for {ctx.symbol}. The following are the upstream "
            "specialist outputs. Use ONLY facts already present here; do not introduce new claims.\n\n"
            f"<specialist_outputs>{json.dumps(priors_serialized, default=str)}</specialist_outputs>\n\n"
            "Produce a BullBearDebateOutput as valid JSON. No tool use."
        )

        async def _no_dispatch(name: str, input_: dict):
            return {"error": "Bull and Bear researchers have no tool access by design."}

        result = await run_tool_loop(
            model,
            LoopParams(
                name=f"{self.side}_researcher",
                system_prompt=prompt_record.text,
                user_message=user,
                tools=[],
                dispatch=_no_dispatch,
                output_schema=BullBearDebateOutput,
            ),
        )

        # Runtime claim-coverage check.
        if result.output is not None:
            allowed = _collect_source_ids(ctx.prior)
            violations = claim_coverage_violations(result.output, allowed)
            if violations:
                log.warning(
                    "debate_introduced_new_sources",
                    side=self.side,
                    new_source_ids=sorted(set(violations)),
                )
        return result


class BullResearcher(_DebateBase):
    name = "bull"
    side = "bull"
    prompt_const = BULL_V1


class BearResearcher(_DebateBase):
    name = "bear"
    side = "bear"
    prompt_const = BEAR_V1
