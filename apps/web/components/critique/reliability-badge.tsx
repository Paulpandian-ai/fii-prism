import { Badge } from "@/components/ui/badge";
import type { ReliabilityRating } from "@/lib/types";

const TONE: Record<ReliabilityRating, "success" | "warning" | "danger" | "neutral"> = {
  high: "success",
  medium: "warning",
  low: "danger",
  do_not_rely: "danger",
};

const LABEL: Record<ReliabilityRating, string> = {
  high: "High reliability",
  medium: "Medium reliability",
  low: "Low reliability",
  do_not_rely: "Do not rely",
};

interface Props {
  rating: ReliabilityRating | null;
  size?: "sm" | "md";
}

/**
 * Color-coded reliability badge used in the critique detail header and the
 * compact "Recent critiques" row on the stock page.
 */
export function ReliabilityBadge({ rating, size = "md" }: Props) {
  if (!rating) {
    return (
      <Badge variant="neutral" className={size === "sm" ? "text-[10px]" : ""}>
        pending
      </Badge>
    );
  }
  return (
    <Badge
      variant={TONE[rating]}
      className={size === "sm" ? "text-[10px]" : ""}
      data-testid={`reliability-badge-${rating}`}
    >
      {LABEL[rating]}
    </Badge>
  );
}
