"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";

import { CritiqueDetail } from "@/components/critique/critique-detail";
import { CritiqueForm } from "@/components/critique/critique-form";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { Card, CardContent } from "@/components/ui/card";
import { useCritique, useRunCritique } from "@/lib/api";

export default function CritiquePage() {
  return (
    <Suspense fallback={<Shell>Loading…</Shell>}>
      <CritiqueRoute />
    </Suspense>
  );
}

function CritiqueRoute() {
  const params = useSearchParams();
  const id = (params.get("id") ?? "").trim() || null;
  const initialSymbol = (params.get("s") ?? "").trim().toUpperCase() || undefined;

  if (id) return <CritiqueDetailView id={id} />;
  return (
    <Shell>
      <CritiqueForm initialSymbol={initialSymbol} />
      <Disclaimer />
    </Shell>
  );
}

function CritiqueDetailView({ id }: { id: string }) {
  const detail = useCritique(id);
  const runMutation = useRunCritique(id);
  const startedRef = useRef(false);

  // Auto-kick the run on first load when the row is still pending.
  useEffect(() => {
    if (startedRef.current) return;
    if (detail.data?.status === "pending") {
      startedRef.current = true;
      runMutation.mutate();
    }
  }, [detail.data?.status, runMutation]);

  if (detail.isLoading) {
    return (
      <Shell>
        <Card>
          <CardContent className="flex items-center gap-2 py-10 text-fii-mute">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading critique…
          </CardContent>
        </Card>
      </Shell>
    );
  }

  if (detail.isError || !detail.data) {
    return (
      <Shell>
        <Card>
          <CardContent className="py-10 text-center text-red-700">
            Could not load critique {id}.
          </CardContent>
        </Card>
      </Shell>
    );
  }

  const row = detail.data;
  const isWorking = row.status === "pending" || row.status === "running";

  return (
    <Shell symbol={row.symbol}>
      {isWorking && (
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-fii-mute">
            <Loader2 className="h-4 w-4 animate-spin" /> Analyzing report against our specialist data…
          </CardContent>
        </Card>
      )}
      <CritiqueDetail row={row} />
      <Disclaimer className="pt-4" />
    </Shell>
  );
}

function Shell({ symbol, children }: { symbol?: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-3xl space-y-6 px-6 py-8">
      {symbol && (
        <div className="flex items-center justify-between">
          <Link
            href={`/stock/?s=${encodeURIComponent(symbol)}`}
            className="text-sm text-fii-blue-700 hover:underline"
          >
            ← Back to {symbol}
          </Link>
        </div>
      )}
      {children}
    </main>
  );
}
