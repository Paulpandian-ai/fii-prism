"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SynthesizeBlockedError, useSynthesizeStock } from "@/lib/api";
import { SPECIALIST_DISPLAY_NAMES, SYNTHESIS_COST_CAP_USD } from "@/lib/specialists";
import type { CachedSpecialistView, SpecialistName } from "@/lib/types";

interface SynthesisSectionProps {
  symbol: string;
  views: CachedSpecialistView[];
}

function asDisplay(canonical: string): string {
  return (
    SPECIALIST_DISPLAY_NAMES[canonical as SpecialistName] ?? canonical
  );
}

/**
 * Final-synthesis card. Disabled until every cacheable specialist is in
 * `state=fresh` AND `status=ok`. When disabled the subtitle lists exactly
 * which specialists are blocking. On a successful run we navigate to the
 * existing analysis-detail page.
 */
export function SynthesisSection({ symbol, views }: SynthesisSectionProps) {
  const router = useRouter();
  const synth = useSynthesizeStock(symbol);
  const [blocked, setBlocked] = useState<{ missing: string[]; stale: string[] } | null>(null);
  const [genericError, setGenericError] = useState<string | null>(null);

  // A specialist is "not ready" if missing/stale OR if its last status is anything
  // other than ok. The synthesize_from_cache backend gate uses identical logic.
  const blockingMissing: string[] = [];
  const blockingStale: string[] = [];
  const blockingErrored: string[] = [];
  for (const v of views) {
    if (v.state === "missing") blockingMissing.push(v.name);
    else if (v.state === "stale") blockingStale.push(v.name);
    else if (v.status && v.status !== "ok") blockingErrored.push(v.name);
  }
  const totalBlockers =
    blockingMissing.length + blockingStale.length + blockingErrored.length;
  const ready = totalBlockers === 0;

  function handleClick() {
    setBlocked(null);
    setGenericError(null);
    synth.mutate(
      { use_premium: false },
      {
        onSuccess: (data) => {
          router.push(`/analysis/?id=${data.analysis_id}`);
        },
        onError: (err) => {
          if (err instanceof SynthesizeBlockedError) {
            setBlocked({ missing: err.missing, stale: err.stale });
          } else {
            setGenericError((err as Error).message);
          }
        },
      },
    );
  }

  return (
    <Card data-testid="synthesis-section" data-ready={ready ? "yes" : "no"}>
      <CardContent className="space-y-3 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-serif text-2xl font-semibold text-fii-navy">Synthesis</h2>
            <p className="mt-1 text-sm text-fii-mute">
              Combines all 8 specialists into a final recommendation, fii_score, and bear case.
            </p>
          </div>
          <Button
            size="lg"
            onClick={handleClick}
            disabled={!ready || synth.isPending}
            data-testid="synthesize-button"
            aria-label={ready ? "Run synthesis" : "Synthesis not ready"}
          >
            {synth.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Synthesizing…
              </>
            ) : (
              <>Run synthesis · ~${SYNTHESIS_COST_CAP_USD.toFixed(2)}</>
            )}
          </Button>
        </div>

        {!ready && (
          <p
            className="rounded-md border border-fii-navy-100 bg-fii-navy-50 px-3 py-2 text-sm text-fii-navy-600"
            data-testid="synthesis-blockers"
          >
            {totalBlockers} specialist{totalBlockers === 1 ? "" : "s"} not ready:{" "}
            <span className="font-medium">
              {[...blockingMissing, ...blockingStale, ...blockingErrored].map(asDisplay).join(", ")}
            </span>
            . Run them before synthesizing.
          </p>
        )}

        {blocked && (
          <p
            className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
            data-testid="synthesis-blocked-error"
          >
            Synthesis blocked by{" "}
            {[...blocked.missing, ...blocked.stale].map(asDisplay).join(", ")}. Re-run them and try
            again.
          </p>
        )}

        {genericError && (
          <p
            className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
            role="alert"
          >
            {genericError}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
