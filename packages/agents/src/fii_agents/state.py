"""LangGraph state for a single deep-dive run.

LangGraph merges parallel node updates via reducers attached to each field.
For specialist-output fields (set once by one node) the default "replace" reducer
works. For accumulators (errors, cost, timings) we attach explicit reducers so
parallel branches don't clobber each other.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from fii_shared import (
    BullBearDebateOutput,
    FundamentalsOutput,
    InsiderFlowOutput,
    MacroOutput,
    MoatOutput,
    NewsSentimentOutput,
    OrchestratorFinalOutput,
    RiskOutput,
    TechnicalOutput,
    ValuationOutput,
)


def _extend(a: list[Any], b: list[Any]) -> list[Any]:
    return list(a) + list(b)


class SpecialistError(TypedDict):
    specialist: str
    kind: str  # "schema_validation_failed" | "tool_error" | "budget_exceeded" | "exception"
    message: str


class AnalysisState(TypedDict, total=False):
    # --- Inputs ---------------------------------------------------------------------------
    symbol: str
    analysis_id: str
    user_id: str

    # --- Context loaded before specialists ------------------------------------------------
    context: dict[str, Any]

    # --- Specialist outputs (set once each) -----------------------------------------------
    fundamentals: FundamentalsOutput | None
    valuation: ValuationOutput | None
    moat: MoatOutput | None
    macro: MacroOutput | None
    technical: TechnicalOutput | None
    news_sentiment: NewsSentimentOutput | None
    insider_flow: InsiderFlowOutput | None
    risk_preliminary: RiskOutput | None
    bull: BullBearDebateOutput | None
    bear: BullBearDebateOutput | None
    risk: RiskOutput | None

    # --- Final synthesis ------------------------------------------------------------------
    final: OrchestratorFinalOutput | None

    # --- Accumulators ---------------------------------------------------------------------
    # Non-fatal errors. Parallel specialists may each add one; the reducer concatenates.
    errors: Annotated[list[SpecialistError], _extend]

    # Running cost total. Each node that spends money adds to this; reducer sums.
    cost_running_total: Annotated[float, operator.add]

    # Token counters.
    tokens_in_total: Annotated[int, operator.add]
    tokens_out_total: Annotated[int, operator.add]
    model_calls_total: Annotated[int, operator.add]

    # Per-specialist timing (ms). Reducer merges dicts.
    timings_ms: Annotated[dict[str, int], lambda a, b: {**a, **b}]
