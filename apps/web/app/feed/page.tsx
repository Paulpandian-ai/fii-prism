"use client";

import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { useEventsList } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import type { RefreshEventType } from "@/lib/types";

const LABELS: Record<RefreshEventType, string> = {
  price_shock: "Price shock",
  news_shock: "News",
  "8k_filed": "8-K filed",
  earnings_release: "Earnings",
  macro_surprise: "Macro surprise",
};

const TONES: Record<RefreshEventType, string> = {
  price_shock: "bg-red-50 text-red-700 ring-red-200",
  news_shock: "bg-amber-50 text-amber-800 ring-amber-200",
  "8k_filed": "bg-indigo-50 text-indigo-800 ring-indigo-200",
  earnings_release: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  macro_surprise: "bg-sky-50 text-sky-800 ring-sky-200",
};

export default function FeedPage() {
  const events = useEventsList({ limit: 100 });

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <Card>
        <CardHeader>
          <CardTitle>Event feed</CardTitle>
          <CardDescription>
            Refresh events from detectors: price shocks, material news, 8-Ks, earnings,
            macro surprises. Watchlisted tickers auto-trigger a quick refresh.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {events.isLoading ? (
            <p className="text-sm text-fii-mute">Loading…</p>
          ) : (events.data ?? []).length === 0 ? (
            <p className="text-sm text-fii-mute">
              Nothing yet. Run{" "}
              <code className="rounded bg-fii-navy-50 px-1">
                fii-ingest simulate-shock -t AAPL
              </code>{" "}
              to inject a demo event.
            </p>
          ) : (
            <ul className="divide-y divide-fii-navy-50">
              {(events.data ?? []).map((e) => (
                <li key={e.event_id} className="py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Link
                          href={`/stock/?s=${e.symbol}`}
                          className="font-semibold text-fii-navy hover:underline"
                        >
                          {e.symbol}
                        </Link>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] ring-1 ring-inset ${TONES[e.event_type]}`}
                        >
                          {LABELS[e.event_type]}
                        </span>
                      </div>
                      <p className="mt-0.5 truncate text-xs text-fii-mute">
                        {describePayload(e.event_type, e.payload)} ·{" "}
                        {fmtRelative(e.detected_at)}
                      </p>
                    </div>
                    {e.analysis_id ? (
                      <Link
                        href={`/analysis/?id=${e.analysis_id}`}
                        className="shrink-0 text-xs text-fii-blue-700 hover:underline"
                      >
                        View refresh →
                      </Link>
                    ) : (
                      <span className="shrink-0 text-[11px] text-fii-mute">
                        not on watchlist
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Disclaimer />
    </main>
  );
}

function describePayload(type: RefreshEventType, payload: Record<string, unknown>): string {
  if (type === "price_shock") {
    const pct = payload.pct_move as number | undefined;
    const z = payload.z as number | undefined;
    if (pct != null && z != null) return `${pct > 0 ? "+" : ""}${pct.toFixed(1)}% (z=${z.toFixed(1)})`;
  }
  if (type === "news_shock" && typeof payload.headline === "string") {
    return payload.headline;
  }
  if (type === "8k_filed" && typeof payload.accession_no === "string") {
    return `Accession ${payload.accession_no}`;
  }
  return "";
}
