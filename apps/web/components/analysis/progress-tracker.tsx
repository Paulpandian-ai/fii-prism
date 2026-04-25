"use client";

import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { useLiveStore } from "@/lib/store";
import { NODE_ORDER, type NodePhase } from "@/lib/types";
import { cn } from "@/lib/cn";

const NICE: Record<string, string> = {
  load_context: "Load context",
  fundamentals: "Fundamentals",
  valuation: "Valuation",
  moat: "Moat",
  macro: "Macro",
  technical: "Technical",
  news_sentiment: "News & Sentiment",
  insider_flow: "Insider Flow",
  risk_preliminary: "Risk (preliminary)",
  bull: "Bull researcher",
  bear: "Bear researcher",
  risk_final: "Risk (final)",
  synthesis: "Synthesis",
  persist: "Persist",
};

function PhaseIcon({ phase }: { phase: NodePhase }) {
  if (phase === "complete")
    return <CheckCircle2 className="h-4 w-4 text-green-600" aria-label="complete" />;
  if (phase === "running")
    return <Loader2 className="h-4 w-4 animate-spin text-fii-blue" aria-label="running" />;
  if (phase === "error") return <XCircle className="h-4 w-4 text-red-600" aria-label="error" />;
  return <Circle className="h-4 w-4 text-fii-navy-100" aria-label="pending" />;
}

export function ProgressTracker({ activeNode, onSelectNode }: {
  activeNode?: string | null;
  onSelectNode?: (n: string) => void;
}) {
  const nodes = useLiveStore((s) => s.nodes);
  return (
    <ol className="space-y-1">
      {NODE_ORDER.map((name) => {
        const np = nodes[name];
        const phase = np?.phase ?? "pending";
        const dur =
          np?.startedAt && np?.completedAt
            ? `${Math.max(0, np.completedAt - np.startedAt)}ms`
            : null;
        return (
          <li key={name}>
            <button
              type="button"
              onClick={() => onSelectNode?.(name)}
              className={cn(
                "flex w-full items-center gap-3 rounded px-2 py-2 text-left text-sm transition-colors",
                activeNode === name ? "bg-fii-blue-50" : "hover:bg-fii-navy-50",
                phase === "pending" && "text-fii-mute",
              )}
            >
              <PhaseIcon phase={phase} />
              <span className="flex-1 truncate">{NICE[name] ?? name}</span>
              {dur && <span className="font-mono text-[11px] text-fii-mute">{dur}</span>}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
