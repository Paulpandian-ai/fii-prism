"use client";

import { Accordion } from "@/components/ui/accordion";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { CitedClaim } from "@/components/citations/cited-claim";
import { ConfidencePill, FiiScoreChip, RecommendationBadge } from "@/components/recommendations";
import { DecisionForm } from "./decision-form";
import { SpecialistCard } from "./specialist-card";
import { StressTable } from "./stress-table";
import { fmtUsd } from "@/lib/format";
import type { AnalysisDetail } from "@/lib/types";

const SPECIALIST_KEYS = [
  "fundamentals",
  "valuation",
  "moat",
  "macro",
  "technical",
  "news",
  "insider",
  "risk",
  "bull",
  "bear",
];

export function FinalLayout({ detail }: { detail: AnalysisDetail }) {
  const final = (detail.specialists?.["__final__"] as Record<string, unknown> | undefined) ??
    extractFinalSummaryFromTopLevel(detail);
  const thesis = (final?.thesis as string | undefined) ?? detail.orchestrator_summary ?? "";
  const whatBuy = final?.what_i_would_buy as string | undefined | null;
  const wrong = (final?.what_could_make_me_wrong as Array<{ claim: string; sources: unknown[]; confidence: string }> | undefined) ?? [];
  const horizon = (final?.time_horizon as string | undefined) ?? "—";
  const stressOutcomes = (final?.stress_outcomes as Record<string, Record<string, unknown>> | undefined) ?? {};
  const cost = (final?.cost_summary as Record<string, number> | undefined) ?? {};
  const specialists = detail.specialists ?? {};
  const bull = (specialists.bull as Record<string, unknown> | undefined) ?? null;
  const bear = (specialists.bear as Record<string, unknown> | undefined) ?? null;

  return (
    <div className="space-y-8">
      <Card>
        <CardContent className="space-y-5 p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-fii-mute">
                Deep-dive · {detail.symbol}
              </p>
              <h1 className="mt-1 font-serif text-3xl font-semibold text-fii-navy">{detail.symbol}</h1>
            </div>
            <div className="flex items-center gap-3">
              <RecommendationBadge value={detail.recommendation ?? null} />
              <ConfidencePill value={detail.confidence ?? null} />
              <FiiScoreChip value={detail.fii_score ?? null} />
            </div>
          </div>
          <div className="flex flex-wrap gap-4 text-xs uppercase tracking-wide text-fii-mute">
            <span>Time horizon: {horizon}</span>
            <span>Cost: {fmtUsd(Number(cost.total_usd ?? detail.total_cost_usd ?? 0))}</span>
            <span>Model calls: {cost.model_calls ?? "—"}</span>
          </div>
        </CardContent>
      </Card>

      {thesis && (
        <Card>
          <CardHeader>
            <CardTitle>Thesis</CardTitle>
            <CardDescription>Synthesis weighed Fundamentals + Valuation as primary inputs.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-base leading-relaxed text-fii-ink">{thesis}</p>
            {whatBuy && (
              <div className="rounded-md bg-fii-navy-50 p-4">
                <div className="mb-1 text-xs uppercase tracking-wide text-fii-mute">What I would buy</div>
                <p className="text-sm text-fii-ink">{whatBuy}</p>
              </div>
            )}
            <Disclaimer />
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <DebateColumn title="Bull Case" data={bull} accent="success" />
        <DebateColumn title="Bear Case" data={bear} accent="danger" />
        <Card>
          <CardHeader>
            <CardTitle>What could make me wrong</CardTitle>
          </CardHeader>
          <CardContent>
            {wrong.length === 0 ? (
              <p className="text-sm text-fii-mute">No counter-thesis recorded.</p>
            ) : (
              <ul className="space-y-1.5">
                {wrong.map((c, i) => (
                  <CitedClaim
                    key={i}
                    claim={{
                      claim: c.claim,
                      // The wire shape carries source dicts; CitedClaim accepts them.
                      sources: c.sources as never,
                      confidence: c.confidence as never,
                    }}
                  />
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Specialist outputs</CardTitle>
          <CardDescription>
            Click a specialist to see its full structured output and citations.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Accordion type="multiple" className="w-full">
            {SPECIALIST_KEYS.map((k) => (
              <SpecialistCard
                key={k}
                name={k}
                output={specialists[k] as Record<string, unknown> | undefined}
              />
            ))}
          </Accordion>
        </CardContent>
      </Card>

      <StressTable scenarios={stressOutcomes} />

      <DecisionForm detail={detail} />
    </div>
  );
}

function DebateColumn({
  title,
  data,
  accent,
}: {
  title: string;
  data: Record<string, unknown> | null;
  accent: "success" | "danger";
}) {
  const caseText = (data?.case as string | undefined) ?? "";
  const evidence =
    (data?.strongest_evidence as Array<{ claim: string; sources: unknown[]; confidence: string }> | undefined) ?? [];
  const trigger = (data?.what_would_change_my_mind as string | undefined) ?? "";
  return (
    <Card>
      <CardHeader>
        <CardTitle className={accent === "success" ? "text-green-700" : "text-red-700"}>
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {caseText && <p className="leading-relaxed text-fii-ink">{caseText}</p>}
        {evidence.length > 0 && (
          <ul className="space-y-1.5">
            {evidence.map((c, i) => (
              <CitedClaim
                key={i}
                claim={{
                  claim: c.claim,
                  sources: c.sources as never,
                  confidence: c.confidence as never,
                }}
              />
            ))}
          </ul>
        )}
        {trigger && (
          <div className="rounded-md bg-fii-navy-50 p-3">
            <div className="text-xs uppercase tracking-wide text-fii-mute">Would change my mind</div>
            <p className="mt-1 text-sm text-fii-ink">{trigger}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function extractFinalSummaryFromTopLevel(detail: AnalysisDetail): Record<string, unknown> {
  // Synthesis is not stored as a specialist row; it lives on the Analysis row itself.
  // Surface what we have so the layout still renders sensibly.
  return {
    thesis: detail.orchestrator_summary,
    cost_summary: { total_usd: detail.total_cost_usd ?? 0 },
  };
}
