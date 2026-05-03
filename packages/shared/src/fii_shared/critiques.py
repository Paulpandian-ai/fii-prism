"""Pydantic schemas for the analyst-report critique flow.

ExtractedReportClaims captures what the report SAYS (call 1 of the critic).
ReportCritique is the structured evaluation we produce against our specialist
cache (call 2). Both are persisted as JSONB on `report_critiques`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fii_shared.primitives import CitedClaim, CitedNumber

# --- Common enums ------------------------------------------------------------------------

ReportSource = Literal["morningstar", "seeking_alpha", "sell_side", "other"]
ReliabilityRating = Literal["high", "medium", "low", "do_not_rely"]
NumericVerdict = Literal["matches", "differs", "unverifiable"]
BiasType = Literal[
    "disclosed_position",
    "paid_promotion",
    "perma_bull",
    "language_bias",
    "selective_data",
    "missing_disclosure",
]
BiasSeverity = Literal["low", "medium", "high"]


# --- Call 1 output: faithful claim extraction --------------------------------------------


class ExtractedReportClaims(BaseModel):
    """What the report says — captured verbatim. The extractor's job is to
    faithfully transcribe claims, not evaluate them. The critic (call 2)
    consumes this against our cached specialist outputs."""

    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(min_length=1, max_length=10)
    report_source: str = Field(max_length=64)
    analyst_name: str | None = Field(default=None, max_length=128)
    publication_date: date | None = None
    # Free-form to tolerate "Buy" / "Outperform" / "Strong Buy" / "Overweight" etc.
    recommendation: str | None = Field(default=None, max_length=64)
    price_target: CitedNumber | None = None
    time_horizon: str | None = Field(default=None, max_length=64)
    bull_case_summary: str = Field(max_length=1600)
    bear_case_summary: str = Field(max_length=1600)
    key_numerical_claims: list[CitedClaim] = Field(default_factory=list)
    key_qualitative_claims: list[CitedClaim] = Field(default_factory=list)
    stated_assumptions: list[str] = Field(default_factory=list)
    analyst_disclosures: list[str] = Field(default_factory=list)


# --- Call 2 output: structured critique --------------------------------------------------


class NumericalAccuracyItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    claim: str = Field(max_length=400)
    report_says: str = Field(max_length=400)
    our_data_says: str = Field(max_length=400)
    verdict: NumericVerdict
    difference_explanation: str | None = Field(default=None, max_length=600)


class BiasIndicator(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    type: BiasType
    evidence: str = Field(max_length=600)
    severity: BiasSeverity


class ReportCritique(BaseModel):
    """Five-section critique of an analyst report against our specialist cache.

    The critic is HONEST, not contrarian: a well-reasoned report should land
    `reliability_rating="high"` even if our specialists disagree on the call.
    Conversely a methodologically-flawed bull report on a stock we like is
    still flagged. The five sections live as separate fields so the UI can
    render each independently.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    critique_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1, max_length=10)
    report_source: str = Field(max_length=64)
    analyst_name: str | None = Field(default=None, max_length=128)

    # Section 1 — fact check
    numerical_accuracy: list[NumericalAccuracyItem] = Field(default_factory=list)

    # Section 2 — reasoning quality
    logical_strengths: list[CitedClaim] = Field(default_factory=list)
    logical_weaknesses: list[CitedClaim] = Field(default_factory=list)

    # Section 3 — hidden assumptions
    unstated_assumptions: list[CitedClaim] = Field(default_factory=list)

    # Section 4 — bias signals
    bias_indicators: list[BiasIndicator] = Field(default_factory=list)

    # Section 5 — what they missed (cites our specialists)
    gaps_in_analysis: list[CitedClaim] = Field(default_factory=list)

    # Final verdict
    reliability_rating: ReliabilityRating
    reliability_rationale: str = Field(max_length=1600)
    one_line_verdict: str = Field(max_length=240)

    cost_usd: float = Field(ge=0)
