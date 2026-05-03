"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { RecentCritiques } from "@/components/stock/recent-critiques";
import { SpecialistGrid } from "@/components/stock/specialist-grid";
import { SynthesisSection } from "@/components/stock/synthesis-section";
import { TotalSpendBadge } from "@/components/stock/total-spend-badge";
import { useStockSpecialists } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import { useAnalysesList } from "@/lib/api";

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
  const symbol = (params.get("s") ?? "").trim().toUpperCase();
  const recent = useAnalysesList(symbol ? { symbol, limit: 5 } : { limit: 0 });
  const list = useStockSpecialists(symbol || null);

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
  const views = list.data ?? [];

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <Card>
        <CardContent className="space-y-4 p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-fii-mute">Stock overview</p>
              <h1 className="mt-1 font-serif text-4xl font-semibold text-fii-navy">{symbol}</h1>
              <p className="mt-1 text-sm text-fii-mute">
                Last analyzed {latest ? fmtRelative(latest.initiated_at) : "never"}
                {latest && (
                  <>
                    {" · "}
                    <Link
                      href={`/analysis/?id=${latest.analysis_id}`}
                      className="text-fii-blue-700 hover:underline"
                    >
                      open most recent
                    </Link>
                  </>
                )}
              </p>
            </div>
            <TotalSpendBadge symbol={symbol} />
          </div>
        </CardContent>
      </Card>

      {list.isLoading ? (
        <SpecialistGridSkeleton />
      ) : list.isError ? (
        <Card>
          <CardContent className="flex flex-col items-start gap-3 p-6">
            <p className="text-sm text-red-700">
              Could not load specialist state: {(list.error as Error).message}
            </p>
            <Button size="sm" onClick={() => list.refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <SpecialistGrid symbol={symbol} views={views} />
          <SynthesisSection symbol={symbol} views={views} />
          <RecentCritiques symbol={symbol} />
        </>
      )}

      <Disclaimer className="pt-4" />
    </main>
  );
}

function SpecialistGridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2" data-testid="specialist-grid-loading">
      {Array.from({ length: 8 }).map((_, i) => (
        <Card key={i}>
          <CardContent className="space-y-3 p-5">
            <div className="h-5 w-32 animate-pulse rounded bg-fii-navy-50" />
            <div className="h-4 w-3/4 animate-pulse rounded bg-fii-navy-50" />
            <div className="h-4 w-1/2 animate-pulse rounded bg-fii-navy-50" />
            <div className="h-8 w-32 animate-pulse rounded bg-fii-navy-50" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
