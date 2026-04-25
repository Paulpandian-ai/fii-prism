"use client";

import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { SearchBar } from "@/components/chrome/search-bar";
import { ConfidencePill, FiiScoreChip, RecommendationBadge } from "@/components/recommendations";
import { useAnalysesList } from "@/lib/api";
import { fmtRelative } from "@/lib/format";

export default function HomePage() {
  const recent = useAnalysesList({ limit: 10 });
  const items = recent.data ?? [];

  return (
    <main className="min-h-screen">
      <section className="bg-fii-navy text-white">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-white/60">
            Factor Impact Intelligence · v2
          </p>
          <h1 className="mt-3 font-serif text-5xl font-semibold leading-tight">
            Multi-agent research for
            <br />
            long-horizon investors.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-relaxed text-white/80">
            Run a deep-dive on any US ticker. Eleven specialist agents debate the thesis,
            cite every claim back to the source filing, and produce a sourced recommendation
            with DFAST-calibrated stress tests in dollar terms.
          </p>
          <div className="mt-8">
            <SearchBar />
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl space-y-6 px-6 py-12">
        <div className="flex items-end justify-between">
          <h2 className="font-serif text-2xl font-semibold text-fii-navy">Recent analyses</h2>
          <Link href="/history/" className="text-sm text-fii-blue-700 hover:underline">
            View all →
          </Link>
        </div>

        {recent.isLoading && (
          <Card>
            <CardContent className="py-10 text-center text-fii-mute">Loading…</CardContent>
          </Card>
        )}
        {recent.isError && (
          <Card>
            <CardContent className="py-10 text-center text-red-600">
              Could not reach the API. Is the FastAPI backend running?
            </CardContent>
          </Card>
        )}
        {!recent.isLoading && !recent.isError && items.length === 0 && (
          <Card>
            <CardContent className="py-10 text-center text-fii-mute">
              No analyses yet. Search for a ticker above to start your first deep-dive.
            </CardContent>
          </Card>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((a) => (
            <Link key={a.analysis_id} href={`/analysis/?id=${a.analysis_id}`}>
              <Card className="h-full transition-shadow hover:shadow-md">
                <CardHeader className="flex-row items-start justify-between gap-2">
                  <div>
                    <CardTitle className="text-base">{a.symbol}</CardTitle>
                    <CardDescription>{fmtRelative(a.initiated_at)}</CardDescription>
                  </div>
                  <RecommendationBadge value={a.recommendation ?? null} />
                </CardHeader>
                <CardContent className="flex items-end justify-between">
                  <FiiScoreChip value={a.fii_score ?? null} />
                  <ConfidencePill value={a.confidence ?? null} />
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>

        <Disclaimer className="pt-6" />
      </section>
    </main>
  );
}
