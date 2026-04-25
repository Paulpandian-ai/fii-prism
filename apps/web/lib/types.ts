// Re-exports from the shared zod schemas + a few API-specific request/response types.
// The backend's response envelope for /analyses is hand-typed here; everything inside
// the `specialists` map matches the shared zod shapes.

export type {
  AgentRunStatus,
  BullBearDebateOutput,
  CitedClaim,
  CitedNumber,
  Confidence,
  FundamentalsOutput,
  InsiderFlowOutput,
  MacroOutput,
  MoatOutput,
  NewsSentimentOutput,
  OrchestratorFinalOutput,
  Recommendation,
  RiskOutput,
  SourceRef,
  TechnicalOutput,
  TimeHorizon,
  ValuationOutput,
} from "@fii/shared/schemas";

import type { OrchestratorFinalOutput, Recommendation } from "@fii/shared/schemas";

// --- API request / response envelopes -----------------------------------------------------

export interface CreateAnalysisRequest {
  symbol: string;
  analysis_type: "deep_dive" | "quick_refresh";
  use_premium_synthesis?: boolean;
  event_type?: string;
  event_id?: string;
}

export interface CreateAnalysisResponse {
  analysis_id: string;
  stream_url: string;
}

export interface AnalysisSummary {
  analysis_id: string;
  symbol: string;
  analysis_type: string;
  status: "pending" | "running" | "succeeded" | "failed" | "canceled";
  initiated_at: string;
  completed_at?: string | null;
  recommendation?: Recommendation | null;
  confidence?: "low" | "medium" | "high" | null;
  fii_score?: number | null;
  total_cost_usd?: number | null;
}

export interface AnalysisDetail extends AnalysisSummary {
  orchestrator_summary?: string | null;
  model_calls_json?: Record<string, unknown>;
  // The backend stores specialist outputs as plain JSON dicts (see Section 5
  // serializer fix). We accept any here; UI components re-validate via zod
  // when they need typed access.
  specialists?: Record<string, unknown>;
}

// --- SSE event shape ----------------------------------------------------------------------

export interface NodeEvent {
  node: string;
  status: "complete" | "started" | "opened" | "closed" | "failed";
  analysis_id?: string;
  message?: string;
}

// --- Watchlist + refresh events -----------------------------------------------------------

export type RefreshEventType =
  | "price_shock"
  | "news_shock"
  | "8k_filed"
  | "earnings_release"
  | "macro_surprise";

export interface WatchlistEntry {
  symbol: string;
  added_at: string;
  notes?: string | null;
}

export interface RefreshEventItem {
  event_id: string;
  symbol: string;
  event_type: RefreshEventType;
  payload: Record<string, unknown>;
  detected_at: string;
  processed_at?: string | null;
  analysis_id?: string | null;
}

// Broker envelope coming off /events/stream — "event" for new refresh events,
// "analysis_updated" when a quick_refresh finishes.
export type BrokerEnvelope =
  | ({
      kind: "event";
      watchlisted: boolean;
    } & RefreshEventItem)
  | {
      kind: "analysis_updated";
      analysis_id: string;
      symbol: string;
      event_type: RefreshEventType;
      event_id: string;
    }
  | { kind: "_open" };

// --- Live-state UI types ------------------------------------------------------------------

export type NodePhase = "pending" | "running" | "complete" | "error";

export interface NodeProgress {
  name: string;
  phase: NodePhase;
  startedAt?: number;
  completedAt?: number;
}

export const NODE_ORDER: readonly string[] = [
  "load_context",
  "fundamentals",
  "valuation",
  "moat",
  "macro",
  "technical",
  "news_sentiment",
  "insider_flow",
  "risk_preliminary",
  "bull",
  "bear",
  "risk_final",
  "synthesis",
  "persist",
] as const;

export type FinalShape = OrchestratorFinalOutput;
