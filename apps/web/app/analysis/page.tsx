"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ProgressTracker } from "@/components/analysis/progress-tracker";
import { CostTicker } from "@/components/analysis/cost-ticker";
import { FinalLayout } from "@/components/analysis/final-layout";
import { SpecialistCard } from "@/components/analysis/specialist-card";
import { Accordion } from "@/components/ui/accordion";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { useAnalysis } from "@/lib/api";
import { useAnalysisStream } from "@/lib/sse";
import { useLiveStore } from "@/lib/store";
import { NODE_ORDER } from "@/lib/types";

export default function AnalysisPage() {
  return (
    <Suspense fallback={<PageShell symbol="…">Loading…</PageShell>}>
      <AnalysisPageInner />
    </Suspense>
  );
}

function AnalysisPageInner() {
  const params = useSearchParams();
  const id = params.get("id");
  const detail = useAnalysis(id);
  useAnalysisStream(id);
  const reset = useLiveStore((s) => s.reset);
  useEffect(() => () => reset(), [reset]);

  if (!id) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-12">
        <Card>
          <CardContent className="py-10 text-center text-fii-mute">
            Missing analysis id. Open an analysis from the home page or search a ticker first.
          </CardContent>
        </Card>
      </main>
    );
  }

  if (detail.isLoading) {
    return <PageShell symbol="…">Loading analysis…</PageShell>;
  }
  if (detail.isError || !detail.data) {
    return (
      <PageShell symbol="—">
        <Card>
          <CardContent className="py-10 text-center text-red-600">
            Could not load analysis {id}.
          </CardContent>
        </Card>
      </PageShell>
    );
  }

  const data = detail.data;
  const isFinal = data.status === "succeeded";

  return (
    <PageShell symbol={data.symbol}>
      {isFinal ? <FinalLayout detail={data} /> : <LiveLayout detail={data} />}
    </PageShell>
  );
}

function PageShell({ symbol, children }: { symbol: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-6xl px-6 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <Link href={`/stock/?s=${symbol}`} className="text-sm text-fii-blue-700 hover:underline">
          ← Back to {symbol}
        </Link>
      </div>
      {children}
      <Disclaimer className="pt-4" />
    </main>
  );
}

function LiveLayout({ detail }: { detail: NonNullable<ReturnType<typeof useAnalysis>["data"]> }) {
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const specialists = detail.specialists ?? {};

  // Map LangGraph node name -> specialist key (some node names differ from specialist keys).
  const NODE_TO_SPEC: Record<string, string> = useMemo(
    () => ({
      fundamentals: "fundamentals",
      valuation: "valuation",
      moat: "moat",
      macro: "macro",
      technical: "technical",
      news_sentiment: "news",
      insider_flow: "insider",
      risk_preliminary: "risk",
      risk_final: "risk",
      bull: "bull",
      bear: "bear",
    }),
    [],
  );

  const activeOutput =
    activeNode && NODE_TO_SPEC[activeNode]
      ? (specialists[NODE_TO_SPEC[activeNode]] as Record<string, unknown> | undefined)
      : null;

  const completedSpecialists = NODE_ORDER.filter((n) => NODE_TO_SPEC[n]).filter((n) => {
    const key = NODE_TO_SPEC[n];
    return key ? specialists[key] : false;
  }).length;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[260px_1fr_240px]">
      {/* Left rail: progress tracker */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pipeline</CardTitle>
        </CardHeader>
        <CardContent>
          <ProgressTracker activeNode={activeNode} onSelectNode={setActiveNode} />
        </CardContent>
      </Card>

      {/* Center: active specialist or "running" indicator */}
      <Card>
        <CardHeader>
          <CardTitle>
            {activeNode ? `Output: ${activeNode}` : "Live progress"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {activeNode && activeOutput && NODE_TO_SPEC[activeNode] ? (
            <Accordion type="single" defaultValue={NODE_TO_SPEC[activeNode] as string}>
              <SpecialistCard name={NODE_TO_SPEC[activeNode] as string} output={activeOutput} />
            </Accordion>
          ) : activeNode ? (
            <p className="text-sm text-fii-mute">
              {activeNode} hasn&apos;t completed yet. Once it does, its full output will load here.
            </p>
          ) : (
            <p className="text-sm text-fii-mute">
              {completedSpecialists} of 11 specialists complete. Click any node on the left
              to inspect its output as it streams in.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Right rail: cost + status */}
      <div className="space-y-4">
        <CostTicker
          totalCostUsd={detail.total_cost_usd ?? 0}
          modelCalls={
            (detail.model_calls_json as Record<string, unknown> | undefined)?.["model_calls"] as
              | number
              | undefined
          }
          status={detail.status}
        />
        <Button variant="secondary" size="sm" className="w-full" disabled>
          Cancel run
        </Button>
        <p className="text-[10px] text-fii-mute">
          Cancellation lands in a follow-up section; for now, just close the tab.
        </p>
      </div>
    </div>
  );
}
