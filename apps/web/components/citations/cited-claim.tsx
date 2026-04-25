"use client";

import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SourceCard } from "./cited-number";
import type { CitedClaim as CitedClaimType } from "@/lib/types";

export function CitedClaim({ claim, withDisclaimer = false }: { claim: CitedClaimType; withDisclaimer?: boolean }) {
  return (
    <li className="flex gap-2">
      <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-fii-navy-100" aria-hidden />
      <div className="space-y-1">
        <Popover>
          <PopoverTrigger asChild>
            <button
              type="button"
              className="text-left text-sm text-fii-ink hover:underline decoration-dotted decoration-fii-navy-100 underline-offset-4"
            >
              {claim.claim} <Info className="ml-1 inline h-3 w-3 text-fii-mute" aria-hidden />
            </button>
          </PopoverTrigger>
          <PopoverContent className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wide text-fii-mute">
              {claim.sources.length} source{claim.sources.length === 1 ? "" : "s"} · confidence: {claim.confidence}
            </div>
            <div className="space-y-3">
              {claim.sources.map((s, i) => (
                <SourceCard key={i} source={s} />
              ))}
            </div>
          </PopoverContent>
        </Popover>
        {withDisclaimer && (
          <p className="text-[10px] uppercase tracking-wide text-fii-mute">
            For educational purposes only. Not investment advice.
          </p>
        )}
      </div>
    </li>
  );
}
