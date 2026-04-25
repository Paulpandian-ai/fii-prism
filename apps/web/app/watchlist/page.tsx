"use client";

import { useState } from "react";
import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Disclaimer } from "@/components/chrome/disclaimer";
import {
  useRemoveWatchlist,
  useUpsertWatchlist,
  useWatchlist,
} from "@/lib/api";
import { fmtRelative } from "@/lib/format";

export default function WatchlistPage() {
  const watchlist = useWatchlist();
  const upsert = useUpsertWatchlist();
  const remove = useRemoveWatchlist();
  const [symbol, setSymbol] = useState("");

  async function handleAdd(evt: React.FormEvent) {
    evt.preventDefault();
    const s = symbol.trim().toUpperCase();
    if (!s) return;
    await upsert.mutateAsync({ symbol: s });
    setSymbol("");
  }

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <Card>
        <CardHeader>
          <CardTitle>Watchlist</CardTitle>
          <CardDescription>
            Price shocks, material news, 8-Ks, and earnings for these tickers trigger an
            automatic quick refresh. Add a symbol to start following it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <form onSubmit={handleAdd} className="flex items-center gap-2">
            <Input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="Ticker (e.g. AAPL)"
              maxLength={10}
              className="max-w-[220px]"
            />
            <Button type="submit" disabled={upsert.isPending || !symbol.trim()}>
              {upsert.isPending ? "Adding…" : "Add"}
            </Button>
            {upsert.isError && (
              <span className="text-xs text-red-600">
                {(upsert.error as Error).message}
              </span>
            )}
          </form>

          {watchlist.isLoading ? (
            <p className="text-sm text-fii-mute">Loading…</p>
          ) : (watchlist.data ?? []).length === 0 ? (
            <p className="text-sm text-fii-mute">No tickers yet.</p>
          ) : (
            <ul className="divide-y divide-fii-navy-50">
              {(watchlist.data ?? []).map((w) => (
                <li
                  key={w.symbol}
                  className="flex items-center justify-between py-3"
                >
                  <div>
                    <Link
                      href={`/stock/?s=${w.symbol}`}
                      className="font-semibold text-fii-navy hover:underline"
                    >
                      {w.symbol}
                    </Link>
                    <p className="text-xs text-fii-mute">
                      Added {fmtRelative(w.added_at)}
                      {w.notes ? ` · ${w.notes}` : ""}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => remove.mutate(w.symbol)}
                    disabled={remove.isPending}
                  >
                    Remove
                  </Button>
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
