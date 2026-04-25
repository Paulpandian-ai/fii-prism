"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { ConfidencePill, FiiScoreChip, RecommendationBadge } from "@/components/recommendations";
import { useAnalysesList } from "@/lib/api";
import { fmtRelative, fmtUsd } from "@/lib/format";
import type { Recommendation } from "@/lib/types";

const REC_FILTERS: { label: string; value: "" | Recommendation }[] = [
  { label: "All", value: "" },
  { label: "Strong Buy", value: "strong_buy" },
  { label: "Buy", value: "buy" },
  { label: "Hold", value: "hold" },
  { label: "Trim", value: "trim" },
  { label: "Sell", value: "sell" },
];

export default function HistoryPage() {
  const list = useAnalysesList({ limit: 100 });
  const [symbolFilter, setSymbolFilter] = useState("");
  const [recFilter, setRecFilter] = useState<"" | Recommendation>("");

  const rows = useMemo(() => {
    const all = list.data ?? [];
    return all.filter((r) => {
      if (symbolFilter && !r.symbol.toLowerCase().includes(symbolFilter.toLowerCase())) return false;
      if (recFilter && r.recommendation !== recFilter) return false;
      return true;
    });
  }, [list.data, symbolFilter, recFilter]);

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <Card>
        <CardHeader>
          <CardTitle>Analysis history</CardTitle>
          <CardDescription>
            Every deep-dive ever run, archived for backtesting your decision trail.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <Input
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value)}
              placeholder="Filter by symbol"
              className="max-w-xs"
            />
            <div className="flex flex-wrap gap-1">
              {REC_FILTERS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  onClick={() => setRecFilter(f.value)}
                  className={
                    "rounded-full border px-3 py-1 text-xs " +
                    (recFilter === f.value
                      ? "border-fii-blue bg-fii-blue text-white"
                      : "border-fii-navy-100 text-fii-mute hover:bg-fii-navy-50")
                  }
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {list.isLoading && <p className="text-sm text-fii-mute">Loading…</p>}
          {list.isError && (
            <p className="text-sm text-red-600">Could not load history. Is the API running?</p>
          )}

          {!list.isLoading && rows.length === 0 && (
            <p className="text-sm text-fii-mute">No matching analyses.</p>
          )}

          {rows.length > 0 && (
            <table className="w-full text-sm">
              <thead className="border-b border-fii-navy-100 text-left text-xs uppercase tracking-wide text-fii-mute">
                <tr>
                  <th className="py-2 pr-4">Symbol</th>
                  <th className="py-2 pr-4">When</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Recommendation</th>
                  <th className="py-2 pr-4">Confidence</th>
                  <th className="py-2 pr-4">Score</th>
                  <th className="py-2 text-right">Cost</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.analysis_id}
                    className="border-b border-fii-navy-50 last:border-0 hover:bg-fii-navy-50"
                  >
                    <td className="py-2 pr-4">
                      <Link href={`/analysis/?id=${r.analysis_id}`} className="font-medium text-fii-navy hover:underline">
                        {r.symbol}
                      </Link>
                    </td>
                    <td className="py-2 pr-4 text-fii-mute">{fmtRelative(r.initiated_at)}</td>
                    <td className="py-2 pr-4">{r.status}</td>
                    <td className="py-2 pr-4">
                      <RecommendationBadge value={r.recommendation ?? null} />
                    </td>
                    <td className="py-2 pr-4">
                      <ConfidencePill value={r.confidence ?? null} />
                    </td>
                    <td className="py-2 pr-4">
                      <FiiScoreChip value={r.fii_score ?? null} />
                    </td>
                    <td className="py-2 text-right font-mono text-fii-mute">
                      {r.total_cost_usd != null ? fmtUsd(r.total_cost_usd) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
      <Disclaimer />
    </main>
  );
}
