"use client";

import { Info } from "lucide-react";
import { fmtNum, fmtPct, fmtUsd } from "@/lib/format";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { CitedNumber as CitedNumberType, SourceRef } from "@/lib/types";

interface CitedNumberProps {
  cited: CitedNumberType;
  className?: string;
}

function formatValue(c: CitedNumberType): string {
  const unit = c.unit.toLowerCase();
  if (unit === "usd") return fmtUsd(c.value, { compact: Math.abs(c.value) >= 1_000_000 });
  if (unit === "ratio" || unit === "percent" || unit === "pct") return fmtPct(c.value, 2);
  return fmtNum(c.value, 2);
}

/**
 * Display a numeric value with a small (i) icon. Hover/click reveals the source ref.
 * Used everywhere a CitedNumber appears in the UI, so every figure on the page is
 * traceable to its origin in one click.
 */
export function CitedNumber({ cited, className }: CitedNumberProps) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={
            "inline-flex items-center gap-1 rounded text-fii-navy hover:bg-fii-navy-50 px-1 -mx-1 " +
            (className ?? "")
          }
        >
          <span>{formatValue(cited)}</span>
          <Info className="h-3 w-3 text-fii-mute" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent className="space-y-2">
        <SourceCard source={cited.source} as_of={cited.as_of} />
      </PopoverContent>
    </Popover>
  );
}

export function SourceCard({ source, as_of }: { source: SourceRef; as_of?: string }) {
  return (
    <div className="space-y-2 text-xs">
      <div className="flex items-center justify-between">
        <span className="rounded bg-fii-navy-50 px-2 py-0.5 font-medium uppercase tracking-wide text-fii-navy-600">
          {source.source_type.replace("_", " ")}
        </span>
        {as_of && <span className="text-fii-mute">as of {as_of}</span>}
      </div>
      <div className="font-mono text-[11px] text-fii-mute break-all">{source.source_id}</div>
      {source.section && (
        <div className="text-fii-ink">
          <span className="text-fii-mute">section: </span>
          {source.section}
        </div>
      )}
      {source.url && (
        <a
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block truncate text-fii-blue-600 hover:underline"
        >
          {source.url}
        </a>
      )}
      <div className="text-fii-mute">retrieved {source.retrieved_at}</div>
    </div>
  );
}
