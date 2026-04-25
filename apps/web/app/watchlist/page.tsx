"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { SearchBar } from "@/components/chrome/search-bar";

export default function WatchlistPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <Card>
        <CardHeader>
          <CardTitle>Watchlist</CardTitle>
          <CardDescription>
            Tracked tickers with their latest FII score and last analysis date.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <SearchBar size="md" />
          <div className="rounded-md border border-dashed border-fii-navy-100 p-8 text-center text-sm text-fii-mute">
            Watchlist persistence (per-user `watchlist` table) lands when Cognito auth ships in
            Section 7. For now the search above opens any ticker; the Stock Overview page&apos;s
            &ldquo;Run Deep Dive&rdquo; button re-runs an analysis on demand.
          </div>
        </CardContent>
      </Card>
      <Disclaimer />
    </main>
  );
}
