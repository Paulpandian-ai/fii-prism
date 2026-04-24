import { z } from "zod";
import { CitedClaim, Confidence } from "./primitives";
import { DfastScenario } from "./specialists";

export const Recommendation = z.enum(["strong_buy", "buy", "hold", "trim", "sell"]);
export type Recommendation = z.infer<typeof Recommendation>;

export const TimeHorizon = z.enum([
  "short (days-weeks)",
  "medium (months)",
  "long (years)",
]);
export type TimeHorizon = z.infer<typeof TimeHorizon>;

export const EDUCATIONAL_DISCLAIMER =
  "For educational purposes only. Not investment advice.";

export const CostSummary = z.object({
  total_usd: z.number().min(0),
  input_tokens: z.number().int().min(0),
  output_tokens: z.number().int().min(0),
  model_calls: z.number().int().min(0),
});
export type CostSummary = z.infer<typeof CostSummary>;

export const OrchestratorFinalOutput = z
  .object({
    symbol: z.string().min(1).max(10),
    analysis_id: z.string().uuid(),
    recommendation: Recommendation,
    confidence: Confidence,
    fii_score: z.number().min(0).max(10),
    thesis: z.string().max(2400),
    what_i_would_buy: z.string().nullable().optional(),
    what_could_make_me_wrong: z.array(CitedClaim).min(3),
    time_horizon: TimeHorizon,
    specialist_summaries: z.record(z.string(), z.string()).default({}),
    stress_outcomes: z.record(z.string(), DfastScenario).default({}),
    cost_summary: CostSummary,
    disclaimer: z.string().default(EDUCATIONAL_DISCLAIMER),
  })
  .superRefine((val, ctx) => {
    if (
      (val.recommendation === "buy" || val.recommendation === "strong_buy") &&
      (val.what_i_would_buy == null || val.what_i_would_buy.trim() === "")
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["what_i_would_buy"],
        message:
          "recommendation is buy/strong_buy but what_i_would_buy is empty — agent must provide concrete price / size / stop.",
      });
    }
    if (val.disclaimer.trim() !== EDUCATIONAL_DISCLAIMER) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["disclaimer"],
        message:
          "disclaimer must be exactly: 'For educational purposes only. Not investment advice.'",
      });
    }
  });
export type OrchestratorFinalOutput = z.infer<typeof OrchestratorFinalOutput>;
