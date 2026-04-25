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

export type ActionTaken =
  | "none"
  | "bought"
  | "added"
  | "held"
  | "trimmed"
  | "sold"
  | "paper_bought"
  | "paper_sold";

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
  action_taken?: ActionTaken;
  action_size_usd?: number | null;
  action_price?: number | null;
  action_at?: string | null;
  action_notes?: string | null;
}

export interface AnalysisDetail extends AnalysisSummary {
  orchestrator_summary?: string | null;
  model_calls_json?: Record<string, unknown>;
  prompt_versions_json?: Record<string, number>;
  // The backend stores specialist outputs as plain JSON dicts (see Section 5
  // serializer fix). We accept any here; UI components re-validate via zod
  // when they need typed access.
  specialists?: Record<string, unknown>;
}

export interface DecisionUpsertRequest {
  action_taken: ActionTaken;
  action_size_usd?: number | null;
  action_price?: number | null;
  action_at?: string | null;
  action_notes?: string | null;
}

// --- Journal -----------------------------------------------------------------------------

export interface JournalSummary {
  decision_count: number;
  paper_count: number;
  real_count: number;
  avg_alpha_by_horizon: Record<string, number | null>;
  hit_rate_by_horizon: Record<string, number | null>;
  win_loss_by_horizon: Record<string, { wins: number; losses: number; pending: number }>;
}

export interface DecisionRow {
  analysis_id: string;
  symbol: string;
  recommendation?: Recommendation | null;
  confidence?: "low" | "medium" | "high" | null;
  fii_score?: number | null;
  action_taken: ActionTaken;
  action_size_usd?: number | null;
  action_price?: number | null;
  action_at?: string | null;
  return_1m?: number | null;
  return_3m?: number | null;
  alpha_1m?: number | null;
  alpha_3m?: number | null;
  hit_1m?: boolean | null;
  hit_3m?: boolean | null;
  dominance?: string | null;
}

export interface BreakdownBucket {
  label: string;
  count: number;
  avg_alpha_1m: number | null;
  avg_alpha_3m: number | null;
  hit_rate_1m: number | null;
  hit_rate_3m: number | null;
}

export interface JournalBreakdowns {
  by_recommendation: BreakdownBucket[];
  by_confidence: BreakdownBucket[];
  by_dominance: BreakdownBucket[];
  by_prompt_version: BreakdownBucket[];
}

// --- Admin / observability ---------------------------------------------------------------

export interface DailyVolume {
  date: string;
  analyses: number;
  avg_cost_usd: number | null;
  total_cost_usd: number;
}

export interface SpecialistHealth {
  specialist: string;
  runs: number;
  avg_duration_ms: number | null;
  error_rate: number;
}

export interface TokenUsageRow {
  model: string | null;
  runs: number;
  tokens_in: number;
  tokens_out: number;
}

export interface AdminStats {
  daily_volume: DailyVolume[];
  specialist_health: SpecialistHealth[];
  token_usage: TokenUsageRow[];
}

export interface CircuitSnapshot {
  name: string;
  state: "closed" | "open" | "half_open";
  consecutive_failures: number;
  cooldown_remaining_s: number;
}

export interface CostCapStatus {
  cap_usd: number;
  spent_today_usd: number;
  remaining_usd: number;
  exceeded: boolean;
  midnight_resets_at: string;
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

// --- Chat / advisor -----------------------------------------------------------------------

export interface ChatSessionSummary {
  session_id: string;
  title: string;
  message_count: number;
  total_cost_usd: number;
  updated_at: string;
  created_at: string;
}

export interface ChatToolCall {
  id?: string;
  name: string;
  input: Record<string, unknown>;
  result?: Record<string, unknown> | null;
}

export interface ChatMessageItem {
  message_id: string;
  role: "user" | "assistant" | "tool";
  content: string | null;
  tool_calls: ChatToolCall[];
  referenced_analysis_ids: string[];
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  model_used?: string | null;
  created_at: string;
}

// SSE frame kinds emitted by POST /chat/{id}/message.
export type ChatStreamFrame =
  | { kind: "user_message_persisted"; message_id: string }
  | { kind: "tool_use_started"; tool_name: string; tool_input: Record<string, unknown> }
  | {
      kind: "tool_result";
      tool_name: string;
      tool_result: Record<string, unknown>;
    }
  | {
      kind: "message_complete";
      final_text: string;
      cost_usd: number;
      tokens_in: number;
      tokens_out: number;
      referenced_analysis_ids: string[];
    }
  | { kind: "assistant_message_persisted"; message_id: string }
  | { kind: "error"; text: string };

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
