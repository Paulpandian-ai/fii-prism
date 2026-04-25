// Shared rendering helpers for recommendation badges + score chips.

import { Badge } from "@/components/ui/badge";
import { fmtNum } from "@/lib/format";
import type { Recommendation } from "@/lib/types";

const REC_VARIANT: Record<Recommendation, "success" | "info" | "neutral" | "warning" | "danger"> = {
  strong_buy: "success",
  buy: "info",
  hold: "neutral",
  trim: "warning",
  sell: "danger",
};

export function RecommendationBadge({ value }: { value: Recommendation | null | undefined }) {
  if (!value) return <Badge variant="neutral">—</Badge>;
  return <Badge variant={REC_VARIANT[value] ?? "neutral"}>{value.replace("_", " ")}</Badge>;
}

export function FiiScoreChip({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-fii-mute">—</span>;
  const tone =
    value >= 8.5
      ? "text-green-700"
      : value >= 7
      ? "text-fii-blue-700"
      : value >= 5
      ? "text-fii-navy"
      : value >= 3.5
      ? "text-amber-700"
      : "text-red-700";
  return (
    <span className={"font-mono text-2xl font-semibold " + tone}>
      {fmtNum(value, 1)}
      <span className="ml-0.5 text-sm text-fii-mute">/10</span>
    </span>
  );
}

export function ConfidencePill({ value }: { value: "low" | "medium" | "high" | null | undefined }) {
  const variant = value === "high" ? "success" : value === "low" ? "warning" : "neutral";
  return <Badge variant={variant}>{value ?? "—"} confidence</Badge>;
}
