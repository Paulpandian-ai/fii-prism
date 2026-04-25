"use client";

import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Disclaimer } from "@/components/chrome/disclaimer";
import {
  useJournalBreakdowns,
  useJournalDecisions,
  useJournalSummary,
} from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import type { BreakdownBucket, DecisionRow } from "@/lib/types";

const HORIZONS = ["1d", "1w", "1m", "3m", "6m", "1y"] as const;

export default function JournalPage() {
  const summary = useJournalSummary();
  const decisions = useJournalDecisions({ limit: 200 });
  const breakdowns = useJournalBreakdowns();

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <Card>
        <CardHeader>
          <CardTitle>Decision journal</CardTitle>
          <CardDescription>
            Every analysis you act on is a tracked prediction. The nightly{" "}
            <code className="rounded bg-fii-navy-50 px-1">fii-ingest compute-outcomes</code>{" "}
            job populates the alpha + hit columns vs SPY at fixed horizons.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {summary.isLoading ? (
            <p className="text-sm text-fii-mute">Loading…</p>
          ) : (
            <SummaryGrid summary={summary.data} />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Decisions</CardTitle>
          <CardDescription>
            Most recent first. Hit (✓) means alpha vs SPY agreed with the recommendation
            at that horizon.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {decisions.isLoading ? (
            <p className="text-sm text-fii-mute">Loading…</p>
          ) : (decisions.data ?? []).length === 0 ? (
            <p className="text-sm text-fii-mute">
              No journaled decisions yet. Run an analysis, then use the &quot;Decision
              journal&quot; form on the analysis page to record what you did.
            </p>
          ) : (
            <DecisionTable rows={decisions.data ?? []} />
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <BreakdownCard
          title="By recommendation"
          description="Did stronger calls actually outperform?"
          buckets={breakdowns.data?.by_recommendation ?? []}
          loading={breakdowns.isLoading}
        />
        <BreakdownCard
          title="By confidence"
          description="Are 'high confidence' calls earning their label?"
          buckets={breakdowns.data?.by_confidence ?? []}
          loading={breakdowns.isLoading}
        />
        <BreakdownCard
          title="By specialist dominance"
          description="When Bull was loudest, how did those decisions perform vs Bear-dominant?"
          buckets={breakdowns.data?.by_dominance ?? []}
          loading={breakdowns.isLoading}
        />
        <BreakdownCard
          title="By prompt-version bundle"
          description="Compare prompt v1 vs v2 across specialists."
          buckets={breakdowns.data?.by_prompt_version ?? []}
          loading={breakdowns.isLoading}
        />
      </div>

      <Disclaimer />
    </main>
  );
}

function SummaryGrid({
  summary,
}: {
  summary: ReturnType<typeof useJournalSummary>["data"];
}) {
  if (!summary) return null;
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Stat label="Total decisions" value={String(summary.decision_count)} />
      <Stat label="Real" value={String(summary.real_count)} />
      <Stat label="Paper" value={String(summary.paper_count)} />
      <div className="lg:col-span-3">
        <p className="mb-2 text-xs uppercase tracking-wide text-fii-mute">
          Hit rate / avg alpha vs SPY
        </p>
        <div className="overflow-x-auto rounded-md border border-fii-navy-100">
          <table className="min-w-full text-sm">
            <thead className="bg-fii-navy-50 text-fii-mute">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Horizon</th>
                {HORIZONS.map((h) => (
                  <th key={h} className="px-3 py-2 text-right font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-fii-navy-50">
              <tr>
                <td className="px-3 py-2 text-left text-fii-mute">Hit rate</td>
                {HORIZONS.map((h) => {
                  const v = summary.hit_rate_by_horizon?.[h];
                  return (
                    <td key={h} className="px-3 py-2 text-right">
                      {v == null ? "—" : `${(v * 100).toFixed(0)}%`}
                    </td>
                  );
                })}
              </tr>
              <tr>
                <td className="px-3 py-2 text-left text-fii-mute">Avg alpha</td>
                {HORIZONS.map((h) => {
                  const v = summary.avg_alpha_by_horizon?.[h];
                  return (
                    <td key={h} className="px-3 py-2 text-right">
                      {v == null ? "—" : `${(v * 100).toFixed(2)}%`}
                    </td>
                  );
                })}
              </tr>
              <tr>
                <td className="px-3 py-2 text-left text-fii-mute">W / L / pending</td>
                {HORIZONS.map((h) => {
                  const v = summary.win_loss_by_horizon?.[h];
                  return (
                    <td key={h} className="px-3 py-2 text-right">
                      {v ? `${v.wins}/${v.losses}/${v.pending}` : "—"}
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-fii-navy-100 px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-fii-mute">{label}</p>
      <p className="mt-1 font-serif text-2xl text-fii-navy">{value}</p>
    </div>
  );
}

function DecisionTable({ rows }: { rows: DecisionRow[] }) {
  return (
    <div className="overflow-x-auto rounded-md border border-fii-navy-100">
      <table className="min-w-full text-sm">
        <thead className="bg-fii-navy-50 text-fii-mute">
          <tr>
            <th className="px-3 py-2 text-left font-medium">When</th>
            <th className="px-3 py-2 text-left font-medium">Symbol</th>
            <th className="px-3 py-2 text-left font-medium">Action</th>
            <th className="px-3 py-2 text-left font-medium">Rec</th>
            <th className="px-3 py-2 text-right font-medium">Size</th>
            <th className="px-3 py-2 text-right font-medium">Entry</th>
            <th className="px-3 py-2 text-right font-medium">Ret 1m</th>
            <th className="px-3 py-2 text-right font-medium">Ret 3m</th>
            <th className="px-3 py-2 text-right font-medium">α 1m</th>
            <th className="px-3 py-2 text-right font-medium">α 3m</th>
            <th className="px-3 py-2 text-center font-medium">Hit 1m</th>
            <th className="px-3 py-2 text-left font-medium">Dom</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-fii-navy-50">
          {rows.map((r) => (
            <tr key={r.analysis_id} className="hover:bg-fii-navy-50/40">
              <td className="px-3 py-2 text-fii-mute">{fmtRelative(r.action_at)}</td>
              <td className="px-3 py-2 font-medium text-fii-navy">{r.symbol}</td>
              <td className="px-3 py-2">{r.action_taken.replace("_", " ")}</td>
              <td className="px-3 py-2">{r.recommendation ?? "—"}</td>
              <td className="px-3 py-2 text-right">{usd(r.action_size_usd)}</td>
              <td className="px-3 py-2 text-right">{usd(r.action_price)}</td>
              <td className="px-3 py-2 text-right">{pct(r.return_1m)}</td>
              <td className="px-3 py-2 text-right">{pct(r.return_3m)}</td>
              <td className="px-3 py-2 text-right">{pct(r.alpha_1m, true)}</td>
              <td className="px-3 py-2 text-right">{pct(r.alpha_3m, true)}</td>
              <td className="px-3 py-2 text-center">
                {r.hit_1m == null ? "—" : r.hit_1m ? "✓" : "✗"}
              </td>
              <td className="px-3 py-2 text-fii-mute">{r.dominance ?? "—"}</td>
              <td className="px-3 py-2">
                <Link
                  href={`/analysis/?id=${r.analysis_id}`}
                  className="text-fii-blue-700 hover:underline"
                >
                  Open →
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BreakdownCard({
  title,
  description,
  buckets,
  loading,
}: {
  title: string;
  description: string;
  buckets: BreakdownBucket[];
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-sm text-fii-mute">Loading…</p>
        ) : buckets.length === 0 ? (
          <p className="text-sm text-fii-mute">Need more decisions for this pivot.</p>
        ) : (
          <div className="overflow-x-auto rounded-md border border-fii-navy-100">
            <table className="min-w-full text-xs">
              <thead className="bg-fii-navy-50 text-fii-mute">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Bucket</th>
                  <th className="px-3 py-2 text-right font-medium">N</th>
                  <th className="px-3 py-2 text-right font-medium">α 1m</th>
                  <th className="px-3 py-2 text-right font-medium">α 3m</th>
                  <th className="px-3 py-2 text-right font-medium">Hit 1m</th>
                  <th className="px-3 py-2 text-right font-medium">Hit 3m</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-fii-navy-50">
                {buckets.map((b) => (
                  <tr key={b.label}>
                    <td className="truncate px-3 py-2 max-w-[180px]" title={b.label}>
                      {b.label}
                    </td>
                    <td className="px-3 py-2 text-right">{b.count}</td>
                    <td className="px-3 py-2 text-right">{pct(b.avg_alpha_1m, true)}</td>
                    <td className="px-3 py-2 text-right">{pct(b.avg_alpha_3m, true)}</td>
                    <td className="px-3 py-2 text-right">
                      {b.hit_rate_1m == null ? "—" : `${(b.hit_rate_1m * 100).toFixed(0)}%`}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {b.hit_rate_3m == null ? "—" : `${(b.hit_rate_3m * 100).toFixed(0)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function pct(v: number | null | undefined, signed = false): string {
  if (v == null) return "—";
  const formatted = `${(v * 100).toFixed(2)}%`;
  if (!signed || v < 0) return formatted;
  return `+${formatted}`;
}

function usd(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toFixed(2)}`;
}
