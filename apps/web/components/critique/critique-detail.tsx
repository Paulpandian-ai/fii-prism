"use client";

import Link from "next/link";

import { CitedClaim } from "@/components/citations/cited-claim";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fmtRelative } from "@/lib/format";
import type { BiasIndicator, CritiqueRow, NumericalAccuracyItem } from "@/lib/types";

import { ReliabilityBadge } from "./reliability-badge";

const VERDICT_TONE: Record<NumericalAccuracyItem["verdict"], "success" | "warning" | "danger" | "neutral"> = {
  matches: "success",
  differs: "danger",
  unverifiable: "warning",
};

const SEVERITY_TONE: Record<BiasIndicator["severity"], "info" | "warning" | "danger"> = {
  low: "info",
  medium: "warning",
  high: "danger",
};

interface Props {
  row: CritiqueRow;
}

/**
 * Renders the persisted ReportCritique JSON across the five sections plus the
 * top-of-page verdict. Defensive against partial data: if `critique` is null
 * (still pending / errored) we show a clean status card instead of crashing.
 */
export function CritiqueDetail({ row }: Props) {
  const c = row.critique;
  if (!c) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <p className="text-sm text-fii-mute">
            Status: <span className="font-medium text-fii-navy">{row.status}</span>
          </p>
          <p className="text-sm text-fii-mute">
            {row.status === "pending" || row.status === "running"
              ? "Analyzing the report — this normally takes 20-60 seconds."
              : "Critique not produced. See server logs for details."}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* Verdict header */}
      <Card>
        <CardContent className="space-y-3 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-fii-mute">Critique</p>
              <h1 className="mt-1 font-serif text-3xl font-semibold text-fii-navy">
                <Link
                  href={`/stock/?s=${encodeURIComponent(row.symbol)}`}
                  className="hover:underline"
                >
                  {row.symbol}
                </Link>{" "}
                <span className="font-sans text-base font-normal text-fii-mute">
                  · {row.report_source ?? "report"}
                </span>
              </h1>
              {c.analyst_name && (
                <p className="mt-1 text-sm text-fii-mute">Analyst: {c.analyst_name}</p>
              )}
            </div>
            <ReliabilityBadge rating={c.reliability_rating} />
          </div>
          <p className="font-serif text-lg italic text-fii-navy" data-testid="one-line-verdict">
            “{c.one_line_verdict}”
          </p>
          <p className="text-sm leading-relaxed text-fii-ink">{c.reliability_rationale}</p>
          <p className="text-xs text-fii-mute">
            {row.report_filename} · cost ${row.cost_usd.toFixed(4)} · completed{" "}
            {fmtRelative(row.completed_at)}
          </p>
        </CardContent>
      </Card>

      {/* Section 1 — numerical accuracy */}
      <Section title="1. Numerical accuracy" testId="section-numerical-accuracy">
        {c.numerical_accuracy.length === 0 ? (
          <Empty>No numerical claims to fact-check.</Empty>
        ) : (
          <ul className="space-y-3">
            {c.numerical_accuracy.map((it, i) => (
              <li
                key={i}
                className="rounded-md border border-fii-navy-100 p-3"
                data-testid="numerical-accuracy-item"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-fii-navy">{it.claim}</span>
                  <Badge variant={VERDICT_TONE[it.verdict]}>{it.verdict}</Badge>
                </div>
                <dl className="mt-2 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
                  <div>
                    <dt className="text-fii-mute">Report says</dt>
                    <dd className="text-fii-ink">{it.report_says}</dd>
                  </div>
                  <div>
                    <dt className="text-fii-mute">Our data says</dt>
                    <dd className="text-fii-ink">{it.our_data_says}</dd>
                  </div>
                </dl>
                {it.difference_explanation && (
                  <p className="mt-2 text-xs text-fii-mute">{it.difference_explanation}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Section 2 — reasoning quality */}
      <Section title="2. Reasoning quality" testId="section-reasoning-quality">
        <ClaimList
          heading="Strengths"
          items={c.logical_strengths}
          emptyText="No specific strengths called out."
        />
        <ClaimList
          heading="Weaknesses"
          items={c.logical_weaknesses}
          emptyText="No reasoning weaknesses surfaced."
          danger
        />
      </Section>

      {/* Section 3 — hidden assumptions */}
      <Section title="3. Hidden assumptions" testId="section-hidden-assumptions">
        {c.unstated_assumptions.length === 0 ? (
          <Empty>The report appears to surface its own assumptions.</Empty>
        ) : (
          <ul className="space-y-1.5">
            {c.unstated_assumptions.map((claim, i) => (
              <CitedClaim key={i} claim={claim} />
            ))}
          </ul>
        )}
      </Section>

      {/* Section 4 — bias signals */}
      <Section title="4. Bias signals" testId="section-bias-signals">
        {c.bias_indicators.length === 0 ? (
          <Empty>No bias indicators detected.</Empty>
        ) : (
          <ul className="space-y-2">
            {c.bias_indicators.map((b, i) => (
              <li
                key={i}
                className="rounded-md border border-fii-navy-100 p-3"
                data-testid="bias-indicator-item"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-fii-navy">
                    {b.type.replaceAll("_", " ")}
                  </span>
                  <Badge variant={SEVERITY_TONE[b.severity]}>{b.severity}</Badge>
                </div>
                <p className="mt-1 text-sm text-fii-ink">{b.evidence}</p>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Section 5 — what they missed */}
      <Section title="5. What they missed" testId="section-gaps-in-analysis">
        {c.gaps_in_analysis.length === 0 ? (
          <Empty>The report addresses everything our specialists flag.</Empty>
        ) : (
          <ul className="space-y-1.5">
            {c.gaps_in_analysis.map((claim, i) => (
              <CitedClaim key={i} claim={claim} />
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
  testId,
}: {
  title: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <Card data-testid={testId}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">{children}</CardContent>
    </Card>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm italic text-fii-mute">{children}</p>;
}

function ClaimList({
  heading,
  items,
  emptyText,
  danger,
}: {
  heading: string;
  items: import("@/lib/types").ReportCritiqueDoc["logical_strengths"];
  emptyText: string;
  danger?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <h4
        className={
          "text-xs uppercase tracking-wide " + (danger ? "text-red-700" : "text-fii-mute")
        }
      >
        {heading}
      </h4>
      {items.length === 0 ? (
        <Empty>{emptyText}</Empty>
      ) : (
        <ul className="space-y-1.5">
          {items.map((claim, i) => (
            <CitedClaim key={i} claim={claim} />
          ))}
        </ul>
      )}
    </div>
  );
}
