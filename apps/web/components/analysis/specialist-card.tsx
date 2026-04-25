"use client";

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { CitedClaim } from "@/components/citations/cited-claim";
import { CitedNumber } from "@/components/citations/cited-number";
import { Disclaimer } from "@/components/chrome/disclaimer";
import { Badge } from "@/components/ui/badge";
import type {
  CitedClaim as CitedClaimType,
  CitedNumber as CitedNumberType,
} from "@/lib/types";

const NICE: Record<string, string> = {
  fundamentals: "Fundamentals",
  valuation: "Valuation",
  moat: "Moat",
  macro: "Macro",
  technical: "Technical",
  news: "News & Sentiment",
  insider: "Insider Flow",
  risk: "Risk",
  bull: "Bull Researcher",
  bear: "Bear Researcher",
};

interface SpecialistCardProps {
  name: string;
  output: Record<string, unknown> | null | undefined;
}

/**
 * Renders a specialist output dict in an accordion. We intentionally render unknown
 * shapes generously: known CitedNumber / CitedClaim / qualitative_summary fields get
 * special treatment; the rest dumps as a key/value list.
 */
export function SpecialistCard({ name, output }: SpecialistCardProps) {
  if (!output) {
    return (
      <AccordionItem value={name}>
        <AccordionTrigger>{NICE[name] ?? name}</AccordionTrigger>
        <AccordionContent>
          <div className="text-sm text-fii-mute">Specialist did not run.</div>
        </AccordionContent>
      </AccordionItem>
    );
  }

  const summary = (output.qualitative_summary as string | undefined) ?? "";
  const confidence = (output.confidence as string | undefined) ?? "—";
  const citedFields = collectCitedNumbers(output);
  const claimFields = collectClaimLists(output);

  return (
    <AccordionItem value={name}>
      <AccordionTrigger className="text-base">
        <div className="flex flex-1 items-center justify-between pr-4">
          <span>{NICE[name] ?? name}</span>
          <Badge variant="neutral">{confidence}</Badge>
        </div>
      </AccordionTrigger>
      <AccordionContent className="space-y-4 pr-2">
        {summary && <p className="text-sm leading-relaxed text-fii-ink">{summary}</p>}

        {citedFields.length > 0 && (
          <div className="grid grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-2">
            {citedFields.map(([k, v]) => (
              <div key={k} className="flex items-baseline justify-between gap-2 border-b border-fii-navy-50 py-1">
                <span className="text-xs uppercase tracking-wide text-fii-mute">{niceKey(k)}</span>
                <CitedNumber cited={v} />
              </div>
            ))}
          </div>
        )}

        {claimFields.map(([fieldName, claims]) =>
          claims.length === 0 ? null : (
            <div key={fieldName}>
              <div className="mb-1 text-xs uppercase tracking-wide text-fii-mute">{niceKey(fieldName)}</div>
              <ul className="space-y-1.5">
                {claims.map((c, i) => (
                  <CitedClaim key={i} claim={c} />
                ))}
              </ul>
            </div>
          ),
        )}

        <Disclaimer />
      </AccordionContent>
    </AccordionItem>
  );
}

function niceKey(k: string): string {
  return k.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
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

function collectCitedNumbers(output: Record<string, unknown>): [string, CitedNumberType][] {
  const out: [string, CitedNumberType][] = [];
  for (const [k, v] of Object.entries(output)) {
    if (isCitedNumber(v)) out.push([k, v]);
  }
  return out;
}

function collectClaimLists(output: Record<string, unknown>): [string, CitedClaimType[]][] {
  const out: [string, CitedClaimType[]][] = [];
  for (const [k, v] of Object.entries(output)) {
    if (Array.isArray(v) && v.every(isCitedClaim)) out.push([k, v]);
  }
  return out;
}
