import { z } from "zod";
import {
  CitedClaim,
  CitedNumber,
  CompParable,
  Confidence,
  FiveForces,
  InsiderMove,
  Trend3,
} from "./primitives";

// Numeric-claim enforcement on qualitative_summary is done at the Pydantic layer in Python
// (see validation.py). We do NOT replicate it in Zod to keep the TS side pure-structural;
// the API boundary always runs Python validation. These zod schemas exist so the Next.js
// client gets typed responses with no business logic.

// --- 1. Fundamentals ---------------------------------------------------------------------

export const FundamentalsOutput = z.object({
  symbol: z.string().min(1).max(10),
  revenue_ttm: CitedNumber,
  revenue_cagr_3y: CitedNumber,
  gross_margin_trend: Trend3,
  gross_margin_latest: CitedNumber,
  operating_margin_latest: CitedNumber,
  fcf_conversion: CitedNumber,
  net_debt_to_ebitda: CitedNumber,
  roic: CitedNumber,
  roic_vs_wacc_spread: CitedNumber,
  interest_coverage: CitedNumber,
  auditor_flags: z.array(CitedClaim).default([]),
  accounting_red_flags: z.array(CitedClaim).default([]),
  qualitative_summary: z.string().max(1600),
  confidence: Confidence,
});
export type FundamentalsOutput = z.infer<typeof FundamentalsOutput>;

// --- 2. Valuation ------------------------------------------------------------------------

export const ValuationOutput = z.object({
  dcf_intrinsic_value_bear: CitedNumber,
  dcf_intrinsic_value_base: CitedNumber,
  dcf_intrinsic_value_bull: CitedNumber,
  current_price: CitedNumber,
  margin_of_safety_pct: CitedNumber,
  wacc_used: CitedNumber,
  terminal_growth_used: CitedNumber,
  revenue_growth_assumption: CitedNumber,
  comparable_multiples: z.array(CompParable).default([]),
  reverse_dcf_implied_growth: CitedNumber,
  sensitivity_table: z.record(z.string(), z.number()).default({}),
  qualitative_summary: z.string().max(1600),
  confidence: Confidence,
});
export type ValuationOutput = z.infer<typeof ValuationOutput>;

// --- 3. Moat -----------------------------------------------------------------------------

export const MoatWidth = z.enum(["none", "narrow", "wide"]);
export const MoatTrend = z.enum(["eroding", "stable", "widening"]);
export const MoatType = z.enum([
  "brand",
  "network_effects",
  "switching_costs",
  "cost_advantage",
  "efficient_scale",
  "intangible_assets",
]);

export const MoatOutput = z.object({
  moat_width: MoatWidth,
  moat_trend: MoatTrend,
  moat_types_present: z.array(MoatType).default([]),
  evidence_for: z.array(CitedClaim).default([]),
  evidence_against: z.array(CitedClaim).min(1),
  five_forces_summary: FiveForces,
  qualitative_summary: z.string().max(1600),
  confidence: Confidence,
});
export type MoatOutput = z.infer<typeof MoatOutput>;

// --- 4. Macro ----------------------------------------------------------------------------

export const MacroRegime = z.enum(["expansion", "late_cycle", "recession", "recovery"]);
export const RatesTrajectory = z.enum(["easing", "neutral", "tightening"]);

export const MacroOutput = z.object({
  regime: MacroRegime,
  regime_evidence: z.array(CitedClaim).min(1),
  rates_trajectory: RatesTrajectory,
  stock_sector_macro_sensitivity: z.record(z.string(), z.number()).default({}),
  top_risks: z.array(CitedClaim).default([]),
  top_tailwinds: z.array(CitedClaim).default([]),
  qualitative_summary: z.string().max(1600),
  confidence: Confidence,
});
export type MacroOutput = z.infer<typeof MacroOutput>;

// --- 5. Technical ------------------------------------------------------------------------

export const TrendShort = z.enum(["up", "sideways", "down"]);
export const MacdSignal = z.enum(["bullish_cross", "bearish_cross", "no_signal"]);
export const TechRegime = z.enum(["trending", "mean_reverting", "choppy"]);
export const Signal = z.enum(["bullish", "neutral", "bearish"]);
export const SignalStrength = z.enum(["weak", "moderate", "strong"]);

export const TechnicalOutput = z.object({
  trend_short: TrendShort,
  trend_medium: TrendShort,
  trend_long: TrendShort,
  rsi_14: z.number().min(0).max(100),
  macd_signal: MacdSignal,
  adx: z.number().min(0).max(100),
  realized_vol_30d: z.number().min(0),
  atr_14: z.number().min(0),
  support_levels: z.array(z.number()).default([]),
  resistance_levels: z.array(z.number()).default([]),
  suggested_entry: CitedNumber,
  suggested_stop: CitedNumber,
  suggested_target: CitedNumber,
  regime: TechRegime,
  signal: Signal,
  signal_strength: SignalStrength,
  qualitative_summary: z.string().max(1600),
  confidence: Confidence,
});
export type TechnicalOutput = z.infer<typeof TechnicalOutput>;

// --- 6. News / Sentiment -----------------------------------------------------------------

export const NewsSentimentOutput = z.object({
  net_sentiment: z.number().min(-1).max(1),
  articles_analyzed: z.number().int().min(0),
  top_positive_themes: z.array(CitedClaim).default([]),
  top_negative_themes: z.array(CitedClaim).default([]),
  anomaly_flags: z.array(CitedClaim).default([]),
  earnings_guidance_changes: z.array(CitedClaim).default([]),
  qualitative_summary: z.string().max(1600),
  confidence: Confidence,
});
export type NewsSentimentOutput = z.infer<typeof NewsSentimentOutput>;

// --- 7. Insider flow ---------------------------------------------------------------------

export const InsiderFlowOutput = z.object({
  net_insider_dollars_90d: CitedNumber,
  cluster_buying: z.boolean(),
  cluster_selling: z.boolean(),
  top_insider_moves: z.array(InsiderMove).default([]),
  institutional_net_change_qoq: CitedNumber,
  activist_presence: z.array(CitedClaim).default([]),
  qualitative_summary: z.string().max(1600),
  confidence: Confidence,
});
export type InsiderFlowOutput = z.infer<typeof InsiderFlowOutput>;

// --- 8. Risk -----------------------------------------------------------------------------

export const GoNoGo = z.enum(["approve", "approve_with_conditions", "reject"]);

export const DfastScenario = z.object({
  name: z.enum(["pullback", "recession", "severe", "sector_shock", "bull_rally"]),
  assumed_move_pct: z.number(),
  position_value_start_usd: z.number(),
  position_value_after_usd: z.number(),
  drawdown_usd: z.number(),
  notes: z.string().nullable().optional(),
});
export type DfastScenario = z.infer<typeof DfastScenario>;

export const RiskOutput = z.object({
  position_size_rec_pct: z.number().min(0).max(100),
  hard_stop_level: CitedNumber,
  max_drawdown_historical: CitedNumber,
  correlation_to_portfolio: z.number().min(-1).max(1),
  liquidity_adequate: z.boolean(),
  concentration_warnings: z.array(CitedClaim).default([]),
  dfast_scenarios: z.record(z.string(), DfastScenario),
  go_no_go: GoNoGo,
  conditions: z.array(z.string()).default([]),
  qualitative_summary: z.string().max(1600),
  confidence: Confidence,
});
export type RiskOutput = z.infer<typeof RiskOutput>;

// --- 9. Bull / Bear debate ---------------------------------------------------------------

export const BullBearDebateOutput = z.object({
  case: z.string().max(3200),
  strongest_evidence: z.array(CitedClaim).min(1),
  weakest_evidence: z.array(CitedClaim).default([]),
  what_would_change_my_mind: z.string().max(1000),
  confidence: Confidence,
});
export type BullBearDebateOutput = z.infer<typeof BullBearDebateOutput>;
