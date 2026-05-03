"use client";

import { Wallet } from "lucide-react";
import { useStockTotalSpend } from "@/lib/api";

interface TotalSpendBadgeProps {
  symbol: string;
}

/**
 * Small inline badge in the stock header showing cumulative cost across the
 * specialist cache + analyses table. Refetches automatically after any
 * specialist run completes (useRunStockSpecialist invalidates this query key).
 */
export function TotalSpendBadge({ symbol }: TotalSpendBadgeProps) {
  const { data, isLoading } = useStockTotalSpend(symbol);
  const total = data?.total_spend_usd ?? 0;
  return (
    <div
      className="inline-flex items-center gap-1.5 rounded-full border border-fii-navy-100 bg-white px-3 py-1 text-xs text-fii-navy-600"
      data-testid="total-spend-badge"
    >
      <Wallet className="h-3 w-3 text-fii-mute" aria-hidden />
      <span>
        Cumulative spend on this stock:{" "}
        <span className="font-medium tabular-nums text-fii-navy">
          {isLoading ? "…" : `$${total.toFixed(2)}`}
        </span>
      </span>
    </div>
  );
}
