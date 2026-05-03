"use client";

import { Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fmtRelative } from "@/lib/format";
import {
  SPECIALIST_COST_CAP_USD,
  SPECIALIST_DESCRIPTIONS,
  SPECIALIST_DISPLAY_NAMES,
  specialistSummary,
} from "@/lib/specialists";
import type { CachedSpecialistView, SpecialistName } from "@/lib/types";

import { SpecialistOutputModal } from "./specialist-output-modal";

interface SpecialistCellProps {
  view: CachedSpecialistView;
  /** Symbol passed through to the modal title. */
  symbol: string;
  /** Called when the user clicks Run / Re-run. The parent owns the mutation. */
  onRun: (name: SpecialistName) => void;
  /** True while this specialist's run is in flight. */
  isRunning: boolean;
  /** Surfaced error from the most recent run attempt; clears on next run. */
  error?: string | null;
}

/**
 * Tone for the status badge. Keep this tightly mapped to the four states the
 * spec calls out — state from the cache freshness gate plus a few error
 * statuses from the runner.
 *   fresh   -> success (green)
 *   stale   -> warning (amber)
 *   missing -> neutral (gray)
 *   error / aborted_cap / input_too_large -> danger (red)
 */
function badgeFor(view: CachedSpecialistView): {
  label: string;
  variant: "success" | "warning" | "neutral" | "danger";
} {
  if (view.status && view.status !== "ok") {
    return { label: view.status, variant: "danger" };
  }
  if (view.state === "fresh") return { label: "fresh", variant: "success" };
  if (view.state === "stale") return { label: "stale", variant: "warning" };
  return { label: "missing", variant: "neutral" };
}

function lastRunLabel(view: CachedSpecialistView): string {
  if (!view.last_run_at) return "never";
  if (view.state === "stale") return `expired · last ${fmtRelative(view.last_run_at)}`;
  return fmtRelative(view.last_run_at);
}

export function SpecialistCell({
  view,
  symbol,
  onRun,
  isRunning,
  error,
}: SpecialistCellProps) {
  const [modalOpen, setModalOpen] = useState(false);
  const name = view.name as SpecialistName;
  const display = SPECIALIST_DISPLAY_NAMES[name];
  const description = SPECIALIST_DESCRIPTIONS[name];
  const summary = specialistSummary(name, view.output);
  const badge = badgeFor(view);
  const cap = SPECIALIST_COST_CAP_USD[name];
  const runLabel = view.last_run_at ? "Re-run" : "Run";
  const hasOutput = view.has_output && view.output !== null;

  return (
    <>
      <Card
        data-testid={`specialist-cell-${name}`}
        data-state={view.state}
        data-status={view.status ?? "none"}
      >
        <CardContent className="space-y-3 p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="font-serif text-lg font-semibold text-fii-navy">{display}</h3>
              <p className="mt-0.5 text-xs text-fii-mute">{description}</p>
            </div>
            <Badge
              variant={badge.variant}
              data-testid={`specialist-badge-${name}`}
              aria-label={`status ${badge.label}`}
            >
              {badge.label}
            </Badge>
          </div>

          <div className="min-h-[2.5rem] text-sm">
            {summary ? (
              <p className="text-fii-ink">{summary}</p>
            ) : (
              <p className="italic text-fii-mute">No analysis yet</p>
            )}
          </div>

          <div className="flex items-center justify-between text-xs text-fii-mute">
            <span>{lastRunLabel(view)}</span>
            {view.cost_usd > 0 && <span>${view.cost_usd.toFixed(4)} spent</span>}
          </div>

          {error && (
            <p
              className="rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700"
              role="alert"
            >
              {error}
            </p>
          )}

          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setModalOpen(true)}
              disabled={!hasOutput || isRunning}
              data-testid={`view-output-${name}`}
            >
              View output
            </Button>
            <Button
              size="sm"
              onClick={() => onRun(name)}
              disabled={isRunning}
              data-testid={`run-button-${name}`}
              aria-label={`${runLabel} ${display} specialist`}
            >
              {isRunning ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running…
                </>
              ) : (
                <>
                  {view.last_run_at ? <RefreshCw className="h-3.5 w-3.5" /> : null}
                  {runLabel} · ~${cap.toFixed(2)}
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {hasOutput && (
        <SpecialistOutputModal
          name={name}
          symbol={symbol}
          view={view}
          open={modalOpen}
          onOpenChange={setModalOpen}
        />
      )}
    </>
  );
}
