"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { useAdminStats, useCircuitBreakers, useCostCap } from "@/lib/api";

const STATE_COLOR: Record<string, string> = {
  closed: "#16a34a",
  half_open: "#f59e0b",
  open: "#dc2626",
};

export default function AdminPage() {
  const stats = useAdminStats();
  const breakers = useCircuitBreakers();
  const cap = useCostCap();

  const daily = stats.data?.daily_volume ?? [];
  const specHealth = stats.data?.specialist_health ?? [];
  const tokens = stats.data?.token_usage ?? [];

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <Card>
        <CardHeader>
          <CardTitle>Admin · observability</CardTitle>
          <CardDescription>
            Single-user MVP dashboard. CloudWatch Logs Insights queries cover the rest of the
            production picture (see RUNBOOK.md).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
            <Stat
              label="Spent today"
              value={cap.data ? `$${cap.data.spent_today_usd.toFixed(4)}` : "—"}
              hint={cap.data ? `cap $${cap.data.cap_usd.toFixed(2)}` : ""}
              tone={cap.data?.exceeded ? "danger" : "ok"}
            />
            <Stat
              label="Remaining today"
              value={cap.data ? `$${cap.data.remaining_usd.toFixed(2)}` : "—"}
            />
            <Stat
              label="Analyses (last 14d)"
              value={String(daily.reduce((sum, r) => sum + r.analyses, 0))}
            />
            <Stat
              label="Avg cost / run"
              value={
                stats.data && daily.length > 0
                  ? `$${(
                      daily.reduce((s, r) => s + r.total_cost_usd, 0) /
                      Math.max(1, daily.reduce((s, r) => s + r.analyses, 0))
                    ).toFixed(4)}`
                  : "—"
              }
            />
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Analyses per day</CardTitle>
            <CardDescription>Last 14 days, all analysis types.</CardDescription>
          </CardHeader>
          <CardContent style={{ height: 280 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={daily} margin={{ top: 10, right: 16, left: -12, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e6eaf2" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="analyses"
                  stroke="#1d4ed8"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Specialist error rate</CardTitle>
            <CardDescription>
              Empty output_json counted as an error. All time.
            </CardDescription>
          </CardHeader>
          <CardContent style={{ height: 280 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={specHealth.map((r) => ({
                  ...r,
                  error_pct: Number((r.error_rate * 100).toFixed(2)),
                }))}
                margin={{ top: 10, right: 16, left: -12, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#e6eaf2" />
                <XAxis dataKey="specialist" tick={{ fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={60} />
                <YAxis tick={{ fontSize: 11 }} unit="%" />
                <Tooltip />
                <Bar dataKey="error_pct" fill="#dc2626" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Specialist latency (avg ms)</CardTitle>
          <CardDescription>Average duration per specialist across all stored runs.</CardDescription>
        </CardHeader>
        <CardContent style={{ height: 280 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={specHealth} margin={{ top: 10, right: 16, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e6eaf2" />
              <XAxis dataKey="specialist" tick={{ fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={60} />
              <YAxis tick={{ fontSize: 11 }} unit="ms" />
              <Tooltip />
              <Bar dataKey="avg_duration_ms" fill="#1d4ed8" />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Token usage by model</CardTitle>
          <CardDescription>
            tokens_in / tokens_out summed across every persisted specialist row.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto rounded-md border border-fii-navy-100">
            <table className="min-w-full text-sm">
              <thead className="bg-fii-navy-50 text-fii-mute">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Model</th>
                  <th className="px-3 py-2 text-right font-medium">Runs</th>
                  <th className="px-3 py-2 text-right font-medium">Tokens in</th>
                  <th className="px-3 py-2 text-right font-medium">Tokens out</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-fii-navy-50">
                {tokens.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-6 text-center text-fii-mute">
                      No specialist runs persisted yet.
                    </td>
                  </tr>
                ) : (
                  tokens.map((t) => (
                    <tr key={String(t.model)}>
                      <td className="px-3 py-2 font-mono text-xs">{t.model ?? "—"}</td>
                      <td className="px-3 py-2 text-right">{t.runs}</td>
                      <td className="px-3 py-2 text-right">{t.tokens_in.toLocaleString()}</td>
                      <td className="px-3 py-2 text-right">{t.tokens_out.toLocaleString()}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>External provider circuit breakers</CardTitle>
          <CardDescription>
            5 consecutive failures opens; 60s cooldown then half-open probe.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="grid grid-cols-1 gap-2 lg:grid-cols-3">
            {(breakers.data ?? []).length === 0 ? (
              <li className="col-span-3 text-sm text-fii-mute">
                No external clients exercised yet this process. Run an analysis or an
                ingestion job and refresh.
              </li>
            ) : (
              (breakers.data ?? []).map((b) => (
                <li key={b.name} className="rounded-md border border-fii-navy-100 px-3 py-2">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-fii-navy">{b.name}</span>
                    <span
                      className="rounded px-2 py-0.5 text-xs text-white"
                      style={{ background: STATE_COLOR[b.state] ?? "#666" }}
                    >
                      {b.state}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-fii-mute">
                    {b.consecutive_failures} consecutive failures
                    {b.state === "open" && b.cooldown_remaining_s > 0
                      ? ` · ${Math.ceil(b.cooldown_remaining_s)}s cooldown`
                      : ""}
                  </p>
                </li>
              ))
            )}
          </ul>
          {/* Hidden tone reference so unused imports stay tree-shakeable. */}
          <span className="hidden">
            <Cell />
            <Legend />
          </span>
        </CardContent>
      </Card>

      <Disclaimer />
    </main>
  );
}

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "ok" | "danger";
}) {
  return (
    <div
      className={
        "rounded-md border px-4 py-3 " +
        (tone === "danger" ? "border-red-300 bg-red-50" : "border-fii-navy-100")
      }
    >
      <p className="text-xs uppercase tracking-wide text-fii-mute">{label}</p>
      <p className="mt-1 font-serif text-2xl text-fii-navy">{value}</p>
      {hint && <p className="text-[11px] text-fii-mute">{hint}</p>}
    </div>
  );
}
