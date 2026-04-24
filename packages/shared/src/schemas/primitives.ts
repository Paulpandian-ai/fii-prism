import { z } from "zod";

// Kept in lockstep with packages/shared/src/fii_shared/primitives.py.

export const Confidence = z.enum(["low", "medium", "high"]);
export type Confidence = z.infer<typeof Confidence>;

export const Trend3 = z.enum(["improving", "stable", "deteriorating"]);
export type Trend3 = z.infer<typeof Trend3>;

export const SourceType = z.enum([
  "sec_filing",
  "fmp_fundamental",
  "polygon_price",
  "fred_series",
  "finnhub_news",
  "calculated",
]);
export type SourceType = z.infer<typeof SourceType>;

// ISO date (YYYY-MM-DD) and ISO datetime strings — Python emits these verbatim.
const IsoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const IsoDateTime = z.string().datetime({ offset: true });

export const SourceRef = z.object({
  source_type: SourceType,
  source_id: z.string().min(1),
  section: z.string().nullable().optional(),
  retrieved_at: IsoDateTime,
  url: z.string().url().nullable().optional(),
});
export type SourceRef = z.infer<typeof SourceRef>;

export const CitedClaim = z.object({
  claim: z.string().min(1).max(2000),
  sources: z.array(SourceRef).min(1),
  confidence: Confidence,
});
export type CitedClaim = z.infer<typeof CitedClaim>;

export const CitedNumber = z.object({
  value: z.number(),
  unit: z.string().min(1).max(32),
  as_of: IsoDate,
  source: SourceRef, // required — no nullable/optional
});
export type CitedNumber = z.infer<typeof CitedNumber>;

// Sub-types shared across specialists.

export const CompParable = z.object({
  peer_symbol: z.string().min(1).max(10),
  ev_ebitda: z.number().nullable().optional(),
  pe_ratio: z.number().nullable().optional(),
});
export type CompParable = z.infer<typeof CompParable>;

export const InsiderMove = z.object({
  name: z.string().min(1),
  role: z.string().nullable().optional(),
  action: z.enum(["buy", "sell", "grant", "exercise", "other"]),
  shares: z.number(),
  dollar_value: z.number(),
  date: IsoDate,
});
export type InsiderMove = z.infer<typeof InsiderMove>;

const ForceLevel = z.enum(["low", "medium", "high"]);
export const FiveForces = z.object({
  threat_new_entrants: ForceLevel,
  bargaining_buyers: ForceLevel,
  bargaining_suppliers: ForceLevel,
  threat_substitutes: ForceLevel,
  competitive_rivalry: ForceLevel,
});
export type FiveForces = z.infer<typeof FiveForces>;

// Re-export helpers used by the parity test.
export { IsoDate, IsoDateTime };
