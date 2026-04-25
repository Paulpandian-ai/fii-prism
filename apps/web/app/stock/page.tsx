"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ConfidencePill, FiiScoreChip, RecommendationBadge } from "@/components/recommendations";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { FactorRadar } from "@/components/analysis/factor-radar";
import { useAnalysesList, useCreateAnalysis } from "@/lib/api";
import { fmtRelative } from "@/lib/format";

export default function StockOverviewPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto max-w-3xl px-6 py-12">
          <Card>
            <CardContent className="py-10 text-center text-fii-mute">Loading…</CardContent>
          </Card>
        </main>
      }
    >
      <StockOverviewInner />
    </Suspense>
  );
}

function StockOverviewInner() {
  const params = useSearchParams();
  const router = useRouter();
  const symbol = (params.get("s") ?? "").trim().toUpperCase();
  const recent = useAnalysesList(symbol ? { symbol, limit: 5 } : { limit: 0 });
  const create = useCreateAnalysis();

  if (!symbol) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-12">
        <Card>
          <CardContent className="py-10 text-center text-fii-mute">
            Pass a ticker via <code className="rounded bg-fii-navy-50 px-1.5 py-0.5">?s=AAPL</code>.
          </CardContent>
        </Card>
      </main>
    );
  }

  const latest = recent.data?.[0];

  function startDeepDive() {
    create.mutate(
      { symbol, analysis_type: "deep_dive" },
      {
        onSuccess: (data) => {
          router.push(`/analysis/?id=${data.analysis_id}`);
        },
      },
    );
  }

  // Until we have per-factor scores breakouts in the API, derive a simple radar from
  // the latest analysis's fii_score (proxy across all factors).
  const proxyScore = latest?.fii_score ?? 0;
  const factorScores = {
    fundamentals: proxyScore,
    valuation: proxyScore,
    moat: proxyScore,
    macro: proxyScore,
    technical: proxyScore,
    risk: proxyScore,
  };

  return (
    <main className="mx-auto max-w-6xl px-6 py-8 space-y-6">
      <Card>
        <CardContent className="space-y-5 p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-fii-mute">
                Stock overview
              </p>
              <h1 className="mt-1 font-serif text-4xl font-semibold text-fii-navy">{symbol}</h1>
              <p className="mt-1 text-sm text-fii-mute">
                Last analyzed {latest ? fmtRelative(latest.initiated_at) : "never"}
              </p>
            </div>
            <div className="flex items-center gap-3">
              <RecommendationBadge value={latest?.recommendation ?? null} />
              <ConfidencePill value={latest?.confidence ?? null} />
              <FiiScoreChip value={latest?.fii_score ?? null} />
            </div>
            <Button size="lg" onClick={startDeepDive} disabled={create.isPending}>
              {create.isPending ? "Starting…" : "Run Deep Dive Analysis"}
            </Button>
          </div>
          {create.isError && (
            <p className="text-sm text-red-600">
              Could not start analysis: {(create.error as Error).message}
            </p>
          )}
        </CardContent>
      </Card>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="financials">Financials</TabsTrigger>
          <TabsTrigger value="factors">Factors</TabsTrigger>
          <TabsTrigger value="alt">Alt Data</TabsTrigger>
          <TabsTrigger value="history">History</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Price chart</CardTitle>
              <CardDescription>1-year daily, 50d / 200d MAs</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex h-64 items-center justify-center rounded-md border border-dashed border-fii-navy-100 text-sm text-fii-mute">
                Price chart loads after Polygon ingestion populates this symbol.
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Six-factor radar</CardTitle>
              <CardDescription>From the latest deep-dive</CardDescription>
            </CardHeader>
            <CardContent>
              <FactorRadar scores={factorScores} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="financials">
          <Card>
            <CardContent className="py-10 text-center text-fii-mute">
              Financial statement view lands in Section 7. Run a deep-dive to surface the
              Fundamentals specialist&apos;s extracted ratios in the meantime.
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="factors">
          <Card>
            <CardContent className="py-10 text-center text-fii-mute">
              Per-factor breakouts use the latest specialist outputs. Run a deep-dive to populate.
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="alt">
          <Card>
            <CardContent className="py-10 text-center text-fii-mute">
              Alt-data lands when patents, USPTO, government-spending feeds are wired (post-MVP).
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="history">
          <Card>
            <CardContent className="space-y-2 p-4">
              {(recent.data ?? []).length === 0 ? (
                <p className="text-sm text-fii-mute">No analyses yet for {symbol}.</p>
              ) : (
                <ul className="divide-y divide-fii-navy-50">
                  {(recent.data ?? []).map((a) => (
                    <li key={a.analysis_id}>
                      <Link
                        href={`/analysis/?id=${a.analysis_id}`}
                        className="flex items-center justify-between gap-3 py-3 hover:bg-fii-navy-50 px-2 -mx-2 rounded"
                      >
                        <span className="text-sm text-fii-mute">{fmtRelative(a.initiated_at)}</span>
                        <RecommendationBadge value={a.recommendation ?? null} />
                        <FiiScoreChip value={a.fii_score ?? null} />
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Disclaimer className="pt-4" />
    </main>
  );
}
