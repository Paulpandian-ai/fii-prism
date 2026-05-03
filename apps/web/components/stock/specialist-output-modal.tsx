"use client";

import { CitedClaim } from "@/components/citations/cited-claim";
import { CitedNumber } from "@/components/citations/cited-number";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { fmtRelative } from "@/lib/format";
import { SPECIALIST_DISPLAY_NAMES } from "@/lib/specialists";
import type {
  CachedSpecialistView,
  CitedClaim as CitedClaimType,
  CitedNumber as CitedNumberType,
  SpecialistName,
} from "@/lib/types";

interface SpecialistOutputModalProps {
  name: SpecialistName;
  symbol: string;
  view: CachedSpecialistView;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function isCitedNumber(v: unknown): v is CitedNumberType {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return typeof o.value === "number" && typeof o.unit === "string" && typeof o.source === "object";
}

function isCitedClaim(v: unknown): v is CitedClaimType {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return typeof o.claim === "string" && Array.isArray(o.sources);
}

function niceKey(k: string): string {
  return k.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

interface SectionedFields {
  citedNumbers: [string, CitedNumberType][];
  claimLists: [string, CitedClaimType[]][];
  qualitative: string | null;
  scalar: [string, unknown][];
}

function partition(output: Record<string, unknown>): SectionedFields {
  const citedNumbers: [string, CitedNumberType][] = [];
  const claimLists: [string, CitedClaimType[]][] = [];
  const scalar: [string, unknown][] = [];
  let qualitative: string | null = null;

  for (const [k, v] of Object.entries(output)) {
    if (k === "qualitative_summary" && typeof v === "string") {
      qualitative = v;
      continue;
    }
    if (isCitedNumber(v)) {
      citedNumbers.push([k, v]);
      continue;
    }
    if (Array.isArray(v) && v.every(isCitedClaim)) {
      claimLists.push([k, v as CitedClaimType[]]);
      continue;
    }
    // Other scalar fields (strings, numbers, bools, plain dicts) — render as JSON.
    if (
      typeof v === "string" ||
      typeof v === "number" ||
      typeof v === "boolean" ||
      v === null ||
      typeof v === "object"
    ) {
      scalar.push([k, v]);
    }
  }
  return { citedNumbers, claimLists, qualitative, scalar };
}

export function SpecialistOutputModal({
  name,
  symbol,
  view,
  open,
  onOpenChange,
}: SpecialistOutputModalProps) {
  const display = SPECIALIST_DISPLAY_NAMES[name];
  const output = view.output ?? {};
  const { citedNumbers, claimLists, qualitative, scalar } = partition(output);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid={`output-modal-${name}`}>
        <DialogHeader>
          <DialogTitle>
            {display}{" "}
            <span className="font-sans text-base font-normal text-fii-mute">· {symbol}</span>
          </DialogTitle>
          <DialogDescription>
            Cached output · last run {fmtRelative(view.last_run_at)}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {qualitative && (
            <section>
              <h4 className="text-xs uppercase tracking-wide text-fii-mute">Summary</h4>
              <p className="mt-1 text-sm leading-relaxed text-fii-ink">{qualitative}</p>
            </section>
          )}

          {citedNumbers.length > 0 && (
            <section>
              <h4 className="text-xs uppercase tracking-wide text-fii-mute">Numbers</h4>
              <div className="mt-1 grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
                {citedNumbers.map(([k, v]) => (
                  <div
                    key={k}
                    className="flex items-baseline justify-between gap-2 border-b border-fii-navy-50 py-1 text-sm"
                  >
                    <span className="text-xs uppercase tracking-wide text-fii-mute">
                      {niceKey(k)}
                    </span>
                    <CitedNumber cited={v} />
                  </div>
                ))}
              </div>
            </section>
          )}

          {claimLists.map(([fieldName, claims]) =>
            claims.length === 0 ? null : (
              <section key={fieldName}>
                <h4 className="text-xs uppercase tracking-wide text-fii-mute">
                  {niceKey(fieldName)}
                </h4>
                <ul className="mt-1 space-y-1.5">
                  {claims.map((c, i) => (
                    <CitedClaim key={i} claim={c} />
                  ))}
                </ul>
              </section>
            ),
          )}

          {scalar.length > 0 && (
            <section>
              <h4 className="text-xs uppercase tracking-wide text-fii-mute">Other fields</h4>
              <dl className="mt-1 grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
                {scalar.map(([k, v]) => (
                  <div key={k} className="flex items-baseline gap-2">
                    <dt className="text-xs uppercase tracking-wide text-fii-mute">
                      {niceKey(k)}
                    </dt>
                    <dd className="font-mono text-xs text-fii-ink break-all">
                      {typeof v === "object" ? JSON.stringify(v) : String(v)}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          )}

          <footer className="border-t border-fii-navy-100 pt-3 text-xs text-fii-mute">
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              <span>cost ${view.cost_usd.toFixed(4)}</span>
              <span>last run {fmtRelative(view.last_run_at)}</span>
              {view.expires_at && <span>expires {fmtRelative(view.expires_at)}</span>}
              {view.status && view.status !== "ok" && (
                <span className="text-red-700">status {view.status}</span>
              )}
            </div>
          </footer>
        </div>
      </DialogContent>
    </Dialog>
  );
}
