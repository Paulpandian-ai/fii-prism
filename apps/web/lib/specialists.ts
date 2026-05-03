/**
 * Display config for the per-specialist UI.
 *
 * Mirrors the backend's `cache_policy` module — when defaults change there,
 * update them here too. The mapping isn't loaded from the API on purpose: a
 * static client-side mirror is fine for "Run · ~$X.XX" button copy, and we'd
 * rather render instantly than wait on a config endpoint.
 */

import type { SpecialistName } from "./types";

/**
 * Per-specialist hard cost cap, mirroring `cache_policy._DEFAULT_COST_CAP_USD`.
 * Used for the "Run · ~$X.XX" button copy as an UPPER BOUND — actual runs
 * usually finish well under this number.
 */
export const SPECIALIST_COST_CAP_USD: Record<SpecialistName, number> = {
  fundamentals: 0.6,
  valuation: 0.4,
  moat: 0.4,
  macro: 0.25,
  technical: 0.25,
  news: 0.25,
  insider: 0.25,
  risk: 0.25,
};

/**
 * Synthesis cost cap (mirrors cache_policy too). Used for the synthesize
 * button copy. Synthesis isn't in the per-specialist registry but is treated
 * as a peer cost-bucket.
 */
export const SYNTHESIS_COST_CAP_USD = 0.4;

export const SPECIALIST_DISPLAY_NAMES: Record<SpecialistName, string> = {
  fundamentals: "Fundamentals",
  valuation: "Valuation",
  moat: "Moat",
  macro: "Macro",
  technical: "Technical",
  news: "News & sentiment",
  insider: "Insider flow",
  risk: "Risk",
};

export const SPECIALIST_DESCRIPTIONS: Record<SpecialistName, string> = {
  fundamentals: "Income, balance, cash-flow, ratios from FMP + 10-K disclosures.",
  valuation: "Bear / base / bull DCFs, WACC, margin of safety.",
  moat: "Competitive durability — adversarially framed.",
  macro: "Cycle regime + rates trajectory + sector sensitivity.",
  technical: "Trend, regime, suggested entry / stop / target.",
  news: "Recent articles + 8-Ks; net sentiment + themes.",
  insider: "Form 4 transactions, 13F changes, cluster signals.",
  risk: "Position sizing, hard stop, DFAST scenarios.",
};

// --- One-line summary extractor ---------------------------------------------------------

/**
 * Pull a short, meaningful one-liner from a cached specialist output. Each
 * specialist has its own canonical fields; we hand-pick the most informative
 * one(s) so the card communicates value at a glance.
 *
 * Returns null if the output is null or doesn't contain the expected fields —
 * the card will fall back to "No analysis yet" muted text.
 */
export function specialistSummary(
  name: SpecialistName,
  output: Record<string, unknown> | null | undefined,
): string | null {
  if (!output) return null;
  switch (name) {
    case "fundamentals":
      return summarizeFundamentals(output);
    case "valuation":
      return summarizeValuation(output);
    case "moat":
      return summarizeMoat(output);
    case "macro":
      return summarizeMacro(output);
    case "technical":
      return summarizeTechnical(output);
    case "news":
      return summarizeNews(output);
    case "insider":
      return summarizeInsider(output);
    case "risk":
      return summarizeRisk(output);
  }
}

function pickNumber(o: Record<string, unknown>, key: string): number | null {
  const v = o[key];
  if (typeof v === "number") return v;
  if (v && typeof v === "object" && "value" in (v as object)) {
    const inner = (v as { value: unknown }).value;
    return typeof inner === "number" ? inner : null;
  }
  return null;
}

function fmtCompactUsd(n: number): string {
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function fmtPct(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`;
}

function summarizeFundamentals(o: Record<string, unknown>): string {
  const rev = pickNumber(o, "revenue_ttm");
  const margin = pickNumber(o, "gross_margin_latest");
  const trend = (o["gross_margin_trend"] as string | undefined) ?? null;
  const parts: string[] = [];
  if (rev !== null) parts.push(`Revenue ${fmtCompactUsd(rev)}`);
  if (margin !== null) parts.push(`gross ${fmtPct(margin)}`);
  if (trend) parts.push(`(${trend})`);
  return parts.length ? parts.join(" · ") : "Output cached.";
}

function summarizeValuation(o: Record<string, unknown>): string {
  const mos = pickNumber(o, "margin_of_safety_pct");
  const base = pickNumber(o, "dcf_intrinsic_value_base");
  const parts: string[] = [];
  if (base !== null) parts.push(`DCF base ${fmtCompactUsd(base)}`);
  if (mos !== null) parts.push(`MoS ${fmtPct(mos)}`);
  return parts.length ? parts.join(" · ") : "Output cached.";
}

function summarizeMoat(o: Record<string, unknown>): string {
  const width = (o["moat_width"] as string | undefined) ?? null;
  const trend = (o["moat_trend"] as string | undefined) ?? null;
  if (width || trend) return `${width ?? "?"} · ${trend ?? "?"}`;
  return "Output cached.";
}

function summarizeMacro(o: Record<string, unknown>): string {
  const regime = (o["regime"] as string | undefined) ?? null;
  const traj = (o["rates_trajectory"] as string | undefined) ?? null;
  if (regime || traj) return `${regime ?? "?"} · rates ${traj ?? "?"}`;
  return "Output cached.";
}

function summarizeTechnical(o: Record<string, unknown>): string {
  const sig = (o["signal"] as string | undefined) ?? null;
  const strength = (o["signal_strength"] as string | undefined) ?? null;
  const regime = (o["regime"] as string | undefined) ?? null;
  if (sig || strength) return `${sig ?? "?"} (${strength ?? "?"}) · ${regime ?? ""}`.trim();
  return "Output cached.";
}

function summarizeNews(o: Record<string, unknown>): string {
  const sent = typeof o["net_sentiment"] === "number" ? (o["net_sentiment"] as number) : null;
  const n = typeof o["articles_analyzed"] === "number" ? (o["articles_analyzed"] as number) : null;
  const parts: string[] = [];
  if (sent !== null) parts.push(`net ${sent > 0 ? "+" : ""}${sent.toFixed(2)}`);
  if (n !== null) parts.push(`${n} articles`);
  return parts.length ? parts.join(" · ") : "Output cached.";
}

function summarizeInsider(o: Record<string, unknown>): string {
  const net = pickNumber(o, "net_insider_dollars_90d");
  const buying = o["cluster_buying"] === true;
  const selling = o["cluster_selling"] === true;
  const parts: string[] = [];
  if (net !== null) parts.push(`net 90d ${net >= 0 ? "+" : "-"}${fmtCompactUsd(Math.abs(net))}`);
  if (buying) parts.push("cluster buying");
  if (selling) parts.push("cluster selling");
  return parts.length ? parts.join(" · ") : "Output cached.";
}

function summarizeRisk(o: Record<string, unknown>): string {
  const size = typeof o["position_size_rec_pct"] === "number"
    ? (o["position_size_rec_pct"] as number)
    : null;
  const go = (o["go_no_go"] as string | undefined) ?? null;
  const parts: string[] = [];
  if (go) parts.push(go.replaceAll("_", " "));
  if (size !== null) parts.push(`size ${size.toFixed(1)}%`);
  return parts.length ? parts.join(" · ") : "Output cached.";
}
