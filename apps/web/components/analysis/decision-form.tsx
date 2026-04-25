"use client";

import { useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUpsertDecision } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import type { ActionTaken, AnalysisDetail } from "@/lib/types";

const ACTIONS: { value: ActionTaken; label: string; group: "real" | "paper" | "none" }[] = [
  { value: "bought", label: "Bought (new position)", group: "real" },
  { value: "added", label: "Added (existing position)", group: "real" },
  { value: "held", label: "Held (no change)", group: "real" },
  { value: "trimmed", label: "Trimmed", group: "real" },
  { value: "sold", label: "Sold (closed position)", group: "real" },
  { value: "paper_bought", label: "Paper buy", group: "paper" },
  { value: "paper_sold", label: "Paper sell", group: "paper" },
  { value: "none", label: "Clear journal entry", group: "none" },
];

export function DecisionForm({ detail }: { detail: AnalysisDetail }) {
  const upsert = useUpsertDecision(detail.analysis_id);
  const [action, setAction] = useState<ActionTaken>(detail.action_taken ?? "none");
  const [size, setSize] = useState<string>(
    detail.action_size_usd ? String(detail.action_size_usd) : "",
  );
  const [price, setPrice] = useState<string>(
    detail.action_price ? String(detail.action_price) : "",
  );
  const [notes, setNotes] = useState<string>(detail.action_notes ?? "");

  const isJournaled = (detail.action_taken ?? "none") !== "none";

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    upsert.mutate({
      action_taken: action,
      action_size_usd: action === "none" ? null : (size ? Number(size) : null),
      action_price: action === "none" ? null : (price ? Number(price) : null),
      action_notes: action === "none" ? null : (notes || null),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Decision journal</CardTitle>
        <CardDescription>
          Record what you did with this analysis. The nightly outcomes job will track
          how it played out vs SPY at 1d / 1w / 1m / 3m / 6m / 1y.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-3 lg:grid-cols-[200px_1fr_1fr_1fr_auto]">
          <select
            value={action}
            onChange={(e) => setAction(e.target.value as ActionTaken)}
            className="h-10 rounded-md border border-fii-navy-100 bg-white px-3 text-sm"
            aria-label="Action taken"
          >
            {ACTIONS.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
          <Input
            placeholder="Size in USD"
            type="number"
            min={0}
            step="0.01"
            value={size}
            onChange={(e) => setSize(e.target.value)}
            disabled={action === "none"}
          />
          <Input
            placeholder="Entry price"
            type="number"
            min={0}
            step="0.0001"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            disabled={action === "none"}
          />
          <Input
            placeholder="Notes (your reasoning)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={action === "none"}
          />
          <Button type="submit" disabled={upsert.isPending}>
            {upsert.isPending ? "Saving…" : "Save"}
          </Button>
        </form>

        {isJournaled && detail.action_at && (
          <p className="mt-3 text-xs text-fii-mute">
            Recorded {fmtRelative(detail.action_at)} ·{" "}
            {detail.action_taken?.replace("_", " ")}
            {detail.action_size_usd ? ` · $${detail.action_size_usd.toFixed(2)}` : ""}
            {detail.action_price ? ` @ $${detail.action_price.toFixed(2)}` : ""}
          </p>
        )}
        {upsert.isError && (
          <p className="mt-3 text-xs text-red-600">
            Could not save: {(upsert.error as Error).message}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
