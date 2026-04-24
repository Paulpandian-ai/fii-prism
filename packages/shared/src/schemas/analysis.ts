import { z } from "zod";

// Analysis lifecycle types (mirror of fii_shared.schemas in Python). Kept separate from the
// agent-output schemas because these are plumbing — request/response envelopes for the API.

export const AgentRunStatus = z.enum([
  "pending",
  "running",
  "succeeded",
  "failed",
  "canceled",
]);
export type AgentRunStatus = z.infer<typeof AgentRunStatus>;

export const Ticker = z.object({
  symbol: z.string().min(1).max(10),
  exchange: z.string().max(16).nullable().optional(),
});
export type Ticker = z.infer<typeof Ticker>;

export const DeepDiveRequest = z.object({
  ticker: Ticker,
  horizon_months: z.number().int().min(1).max(240).default(36),
  notes: z.string().max(2000).nullable().optional(),
});
export type DeepDiveRequest = z.infer<typeof DeepDiveRequest>;

export const AnalysisRun = z.object({
  run_id: z.string(),
  ticker: Ticker,
  status: AgentRunStatus,
  started_at: z.string().datetime(),
  finished_at: z.string().datetime().nullable().optional(),
  step_functions_execution_arn: z.string().nullable().optional(),
  cost_usd: z.number().min(0).nullable().optional(),
});
export type AnalysisRun = z.infer<typeof AnalysisRun>;
