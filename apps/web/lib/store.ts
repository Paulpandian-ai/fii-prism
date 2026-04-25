/**
 * Live-analysis Zustand store.
 *
 * Tracks per-node phase + timing, accumulated cost, elapsed time, and the
 * connection state for the SSE stream. Server-side data (final output, specialist
 * outputs) lives in TanStack Query's cache; this store is purely UI state.
 */

import { create } from "zustand";
import { NODE_ORDER, type NodeEvent, type NodeProgress } from "./types";

type ConnState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error";

interface LiveState {
  analysisId: string | null;
  startedAt: number | null;
  conn: ConnState;
  nodes: Record<string, NodeProgress>;
  setAnalysis: (id: string | null) => void;
  setConn: (s: ConnState) => void;
  applyEvent: (e: NodeEvent) => void;
  reset: () => void;
}

const initialNodes = (): Record<string, NodeProgress> => {
  const out: Record<string, NodeProgress> = {};
  for (const name of NODE_ORDER) {
    out[name] = { name, phase: "pending" };
  }
  return out;
};

export const useLiveStore = create<LiveState>((set) => ({
  analysisId: null,
  startedAt: null,
  conn: "idle",
  nodes: initialNodes(),
  setAnalysis: (id) =>
    set(() => ({
      analysisId: id,
      startedAt: id ? Date.now() : null,
      nodes: initialNodes(),
      conn: "idle",
    })),
  setConn: (s) => set({ conn: s }),
  applyEvent: (e) =>
    set((state) => {
      const now = Date.now();
      const nodes = { ...state.nodes };
      const known = nodes[e.node];
      if (!known) return state;
      if (e.status === "complete") {
        nodes[e.node] = {
          ...known,
          phase: "complete",
          completedAt: now,
          startedAt: known.startedAt ?? now,
        };
        // Mark the next pending node as running for visual continuity.
        const idx = NODE_ORDER.indexOf(e.node);
        for (let i = idx + 1; i < NODE_ORDER.length; i += 1) {
          const next = NODE_ORDER[i];
          if (next && nodes[next]?.phase === "pending") {
            nodes[next] = { ...nodes[next], phase: "running", startedAt: now };
            break;
          }
        }
      } else if (e.status === "started") {
        nodes[e.node] = { ...known, phase: "running", startedAt: now };
      } else if (e.status === "failed") {
        nodes[e.node] = { ...known, phase: "error", completedAt: now };
      }
      return { nodes };
    }),
  reset: () =>
    set({ analysisId: null, startedAt: null, conn: "idle", nodes: initialNodes() }),
}));
