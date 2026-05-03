"use client";

import Link from "next/link";

import { ReliabilityBadge } from "@/components/critique/reliability-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useStockCritiques } from "@/lib/api";
import { fmtRelative } from "@/lib/format";

interface Props {
  symbol: string;
}

/**
 * Compact list of the 3 most recent analyst-report critiques for this ticker.
 * Slots below the synthesis section on /stock/?s={symbol}. Hidden when no
 * critiques exist — the upload entry point lives on /critique.
 */
export function RecentCritiques({ symbol }: Props) {
  const { data, isLoading } = useStockCritiques(symbol);

  return (
    <Card data-testid="recent-critiques">
      <CardContent className="space-y-3 p-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-serif text-xl font-semibold text-fii-navy">Recent critiques</h2>
            <p className="text-sm text-fii-mute">
              Analyst PDFs you uploaded for {symbol}, evaluated against our specialists.
            </p>
          </div>
          <Button variant="secondary" size="sm" asChild>
            <Link href={`/critique/?s=${encodeURIComponent(symbol)}`}>Upload a report</Link>
          </Button>
        </div>

        {isLoading ? (
          <p className="text-sm text-fii-mute">Loading…</p>
        ) : !data || data.length === 0 ? (
          <p className="text-sm italic text-fii-mute">No critiques yet for {symbol}.</p>
        ) : (
          <ul className="space-y-2">
            {data.slice(0, 3).map((c) => (
              <li
                key={c.critique_id}
                className="rounded-md border border-fii-navy-100"
                data-testid="recent-critique-item"
              >
                <Link
                  href={`/critique/?id=${encodeURIComponent(c.critique_id)}`}
                  className="block p-3 hover:bg-fii-navy-50"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-fii-navy">
                      {c.report_filename}
                    </span>
                    <ReliabilityBadge rating={c.reliability_rating} size="sm" />
                  </div>
                  <p className="mt-1 text-xs text-fii-mute">
                    {c.report_source ?? "report"} · {fmtRelative(c.created_at)}
                    {c.status !== "ok" && ` · status ${c.status}`}
                  </p>
                  {c.one_line_verdict && (
                    <p className="mt-1 text-sm italic text-fii-ink">“{c.one_line_verdict}”</p>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
