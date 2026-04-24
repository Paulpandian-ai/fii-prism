"""Structured outputs for each specialist agent.

Every specialist output:
- Has a `confidence` field (Literal["low","medium","high"] via Confidence enum).
- Has a `qualitative_summary: str` capped at ~200 words.
- Extends NumericClaimsMixin so prose numbers must be cited.
- Declares `__numeric_claim_text_fields__` listing which prose fields to scan.

Numeric values that an agent derives from the underlying data (RSI, realized vol,
the percent margin of safety) appear as plain floats — the source is implicit in the
data (polygon_price for technical values, etc.). Claims about the company that come
from a filing or fundamental payload must use CitedNumber.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fii_shared.primitives import (
    CitedClaim,
    CitedNumber,
    CompParable,
    Confidence,
    FiveForces,
    InsiderMove,
    Trend3,
)
from fii_shared.validation import NumericClaimsMixin

# --- 1. Fundamentals ---------------------------------------------------------------------


class FundamentalsOutput(NumericClaimsMixin):
    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("qualitative_summary",)

    symbol: str = Field(min_length=1, max_length=10)

    revenue_ttm: CitedNumber
    revenue_cagr_3y: CitedNumber
    gross_margin_trend: Trend3
    gross_margin_latest: CitedNumber
    operating_margin_latest: CitedNumber
    fcf_conversion: CitedNumber
    net_debt_to_ebitda: CitedNumber
    roic: CitedNumber
    roic_vs_wacc_spread: CitedNumber
    interest_coverage: CitedNumber

    auditor_flags: list[CitedClaim] = Field(default_factory=list)
    accounting_red_flags: list[CitedClaim] = Field(default_factory=list)

    qualitative_summary: str = Field(max_length=1600)
    confidence: Confidence


# --- 2. Valuation ------------------------------------------------------------------------


class ValuationOutput(NumericClaimsMixin):
    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("qualitative_summary",)

    dcf_intrinsic_value_bear: CitedNumber
    dcf_intrinsic_value_base: CitedNumber
    dcf_intrinsic_value_bull: CitedNumber
    current_price: CitedNumber
    margin_of_safety_pct: CitedNumber
    wacc_used: CitedNumber
    terminal_growth_used: CitedNumber
    revenue_growth_assumption: CitedNumber

    comparable_multiples: list[CompParable] = Field(default_factory=list)
    reverse_dcf_implied_growth: CitedNumber

    # Map of sensitivity label → resulting intrinsic value in USD/share.
    # e.g. {"wacc_plus_1pct": 142.3, "terminal_plus_1pct": 155.8}
    sensitivity_table: dict[str, float] = Field(default_factory=dict)

    qualitative_summary: str = Field(max_length=1600)
    confidence: Confidence


# --- 3. Moat -----------------------------------------------------------------------------


MoatWidth = Literal["none", "narrow", "wide"]
MoatTrend = Literal["eroding", "stable", "widening"]
MoatType = Literal[
    "brand",
    "network_effects",
    "switching_costs",
    "cost_advantage",
    "efficient_scale",
    "intangible_assets",
]


class MoatOutput(NumericClaimsMixin):
    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("qualitative_summary",)

    moat_width: MoatWidth
    moat_trend: MoatTrend
    moat_types_present: list[MoatType] = Field(default_factory=list)

    evidence_for: list[CitedClaim] = Field(default_factory=list)
    # REQUIRED — adversarial frame. Every moat thesis needs at least one counter-point.
    evidence_against: list[CitedClaim] = Field(min_length=1)

    five_forces_summary: FiveForces

    qualitative_summary: str = Field(max_length=1600)
    confidence: Confidence


# --- 4. Macro ----------------------------------------------------------------------------


MacroRegime = Literal["expansion", "late_cycle", "recession", "recovery"]
RatesTrajectory = Literal["easing", "neutral", "tightening"]


class MacroOutput(NumericClaimsMixin):
    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("qualitative_summary",)

    regime: MacroRegime
    regime_evidence: list[CitedClaim] = Field(min_length=1)
    rates_trajectory: RatesTrajectory

    # Correlation of this stock/sector's return to each macro factor. Bounded to [-1, 1].
    stock_sector_macro_sensitivity: dict[str, float] = Field(default_factory=dict)

    top_risks: list[CitedClaim] = Field(default_factory=list)
    top_tailwinds: list[CitedClaim] = Field(default_factory=list)

    qualitative_summary: str = Field(max_length=1600)
    confidence: Confidence


# --- 5. Technical ------------------------------------------------------------------------


TrendShort = Literal["up", "sideways", "down"]
MacdSignal = Literal["bullish_cross", "bearish_cross", "no_signal"]
TechRegime = Literal["trending", "mean_reverting", "choppy"]
Signal = Literal["bullish", "neutral", "bearish"]
SignalStrength = Literal["weak", "moderate", "strong"]


class TechnicalOutput(NumericClaimsMixin):
    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("qualitative_summary",)

    trend_short: TrendShort  # 20d
    trend_medium: TrendShort  # 50d
    trend_long: TrendShort  # 200d

    # Derived indicators: no CitedNumber wrapper because the source IS the price series
    # (polygon_price) and these are computed. Bounds enforced where natural.
    rsi_14: float = Field(ge=0, le=100)
    macd_signal: MacdSignal
    adx: float = Field(ge=0, le=100)
    realized_vol_30d: float = Field(ge=0)
    atr_14: float = Field(ge=0)

    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)

    # Actionable price levels DO cite — these are the agent's concrete recommendations.
    suggested_entry: CitedNumber
    suggested_stop: CitedNumber
    suggested_target: CitedNumber

    regime: TechRegime
    signal: Signal
    signal_strength: SignalStrength

    qualitative_summary: str = Field(max_length=1600)
    confidence: Confidence


# --- 6. News / Sentiment -----------------------------------------------------------------


class NewsSentimentOutput(NumericClaimsMixin):
    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("qualitative_summary",)

    net_sentiment: float = Field(ge=-1.0, le=1.0)
    articles_analyzed: int = Field(ge=0)

    top_positive_themes: list[CitedClaim] = Field(default_factory=list)
    top_negative_themes: list[CitedClaim] = Field(default_factory=list)
    anomaly_flags: list[CitedClaim] = Field(default_factory=list)
    earnings_guidance_changes: list[CitedClaim] = Field(default_factory=list)

    qualitative_summary: str = Field(max_length=1600)
    confidence: Confidence


# --- 7. Insider flow ---------------------------------------------------------------------


class InsiderFlowOutput(NumericClaimsMixin):
    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("qualitative_summary",)

    net_insider_dollars_90d: CitedNumber
    cluster_buying: bool
    cluster_selling: bool
    top_insider_moves: list[InsiderMove] = Field(default_factory=list)
    institutional_net_change_qoq: CitedNumber
    activist_presence: list[CitedClaim] = Field(default_factory=list)

    qualitative_summary: str = Field(max_length=1600)
    confidence: Confidence


# --- 8. Risk -----------------------------------------------------------------------------


GoNoGo = Literal["approve", "approve_with_conditions", "reject"]


class DfastScenario(BaseModel):
    """One row of the DFAST-style stress test. Kept aligned with v1's scenarios."""

    name: Literal["pullback", "recession", "severe", "sector_shock", "bull_rally"]
    assumed_move_pct: float  # e.g. -0.20 for a 20% pullback
    position_value_start_usd: float
    position_value_after_usd: float
    drawdown_usd: float
    notes: str | None = None


class RiskOutput(NumericClaimsMixin):
    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("qualitative_summary",)

    position_size_rec_pct: float = Field(ge=0, le=100)
    hard_stop_level: CitedNumber
    max_drawdown_historical: CitedNumber
    correlation_to_portfolio: float = Field(ge=-1.0, le=1.0)
    liquidity_adequate: bool
    concentration_warnings: list[CitedClaim] = Field(default_factory=list)

    # Dict keyed by DfastScenario.name so consumers can index by scenario directly.
    dfast_scenarios: dict[str, DfastScenario]

    go_no_go: GoNoGo
    conditions: list[str] = Field(default_factory=list)

    qualitative_summary: str = Field(max_length=1600)
    confidence: Confidence


# --- 9. Bull / Bear debate ---------------------------------------------------------------


class BullBearDebateOutput(NumericClaimsMixin):
    """Used for both the Bull researcher and the Bear researcher. The orchestrator
    knows which is which by which slot the output occupies.
    """

    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("case", "what_would_change_my_mind")

    case: str = Field(max_length=3200, description="Up to 400 words of narrative")
    strongest_evidence: list[CitedClaim] = Field(min_length=1)
    weakest_evidence: list[CitedClaim] = Field(default_factory=list)
    what_would_change_my_mind: str = Field(max_length=1000)
    confidence: Confidence
