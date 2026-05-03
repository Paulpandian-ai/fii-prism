"use client";

import { useState } from "react";

import { useRunStockSpecialist } from "@/lib/api";
import { SPECIALIST_ORDER } from "@/lib/types";
import type { CachedSpecialistView, SpecialistName } from "@/lib/types";

import { SpecialistCell } from "./specialist-cell";

interface SpecialistGridProps {
  symbol: string;
  views: CachedSpecialistView[];
}

/**
 * Renders a card per cacheable specialist in canonical order. The query for
 * the views array is owned by the parent page so the SynthesisSection sees
 * the same data without an extra round-trip.
 */
export function SpecialistGrid({ symbol, views }: SpecialistGridProps) {
  const run = useRunStockSpecialist(symbol);
  const [pendingName, setPendingName] = useState<SpecialistName | null>(null);
  const [errors, setErrors] = useState<Partial<Record<SpecialistName, string>>>({});

  function handleRun(name: SpecialistName) {
    setErrors((prev) => ({ ...prev, [name]: undefined }));
    setPendingName(name);
    run.mutate(
      { name, force: true },
      {
        onSettled: () => setPendingName(null),
        onError: (err) =>
          setErrors((prev) => ({ ...prev, [name]: (err as Error).message })),
      },
    );
  }

  // Backend may emit the specialists in any order; render in canonical UI order.
  const byName = new Map<SpecialistName, CachedSpecialistView>();
  for (const v of views) {
    if ((SPECIALIST_ORDER as readonly string[]).includes(v.name)) {
      byName.set(v.name as SpecialistName, v);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2" data-testid="specialist-grid">
      {SPECIALIST_ORDER.map((name) => {
        const view = byName.get(name);
        if (!view) return null;
        return (
          <SpecialistCell
            key={name}
            view={view}
            symbol={symbol}
            onRun={handleRun}
            isRunning={pendingName === name}
            error={errors[name] ?? null}
          />
        );
      })}
    </div>
  );
}
