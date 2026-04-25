"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fmtUsd, elapsed } from "@/lib/format";
import { useLiveStore } from "@/lib/store";

interface CostTickerProps {
  totalCostUsd?: number | null;
  tokensIn?: number | null;
  tokensOut?: number | null;
  modelCalls?: number | null;
  status: "running" | "succeeded" | "failed" | "pending" | "canceled";
}

export function CostTicker({ totalCostUsd, tokensIn, tokensOut, modelCalls, status }: CostTickerProps) {
  const startedAt = useLiveStore((s) => s.startedAt);
  const conn = useLiveStore((s) => s.conn);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (status !== "running" && status !== "pending") return;
    const id = setInterval(() => setTick((t) => t + 1), 500);
    return () => clearInterval(id);
  }, [status]);

  const connBadge =
    conn === "open"
      ? <Badge variant="success">live</Badge>
      : conn === "reconnecting"
      ? <Badge variant="warning">reconnecting…</Badge>
      : conn === "error"
      ? <Badge variant="danger">error</Badge>
      : <Badge variant="neutral">{conn}</Badge>;

  return (
    <Card className="space-y-0">
      <CardContent className="space-y-3 p-5">
        <div className="flex items-center justify-between">
          <span className="text-xs uppercase tracking-wide text-fii-mute">Status</span>
          {connBadge}
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-fii-mute">Elapsed</div>
          <div className="font-mono text-2xl text-fii-navy" aria-live="polite">
            {/* tick triggers re-render; suppress unused-var warning by referencing it */}
            <span className="sr-only">{tick}</span>
            {elapsed(startedAt)}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-fii-mute">Cost so far</div>
          <div className="font-mono text-lg text-fii-navy">
            {totalCostUsd != null ? fmtUsd(totalCostUsd) : "$0.00"}
          </div>
        </div>
        <div className="flex justify-between text-xs text-fii-mute">
          <span>tokens in: {(tokensIn ?? 0).toLocaleString()}</span>
          <span>out: {(tokensOut ?? 0).toLocaleString()}</span>
        </div>
        <div className="text-xs text-fii-mute">model calls: {modelCalls ?? 0}</div>
      </CardContent>
    </Card>
  );
}
