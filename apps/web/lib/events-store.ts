/**
 * Notifications store.
 *
 * Keeps the most recent refresh events + analysis-update envelopes received from
 * the /events/stream SSE feed, plus an unread counter for the header bell. The
 * store is intentionally tiny: persistent event history lives server-side in
 * the `refresh_events` table.
 */

import { create } from "zustand";
import type { BrokerEnvelope } from "./types";

export interface NotificationItem {
  key: string;
  kind: "event" | "analysis_updated";
  symbol: string;
  event_type: string;
  event_id: string;
  analysis_id?: string;
  watchlisted?: boolean;
  headline?: string;
  receivedAt: number;
}

interface EventsState {
  items: NotificationItem[];
  unread: number;
  conn: "idle" | "connecting" | "open" | "reconnecting" | "closed" | "error";
  lastAnalysisUpdated: {
    analysis_id: string;
    symbol: string;
    event_type: string;
    at: number;
  } | null;
  setConn: (c: EventsState["conn"]) => void;
  push: (e: BrokerEnvelope) => void;
  markAllRead: () => void;
  clear: () => void;
}

const MAX_ITEMS = 50;

export const useEventsStore = create<EventsState>((set) => ({
  items: [],
  unread: 0,
  conn: "idle",
  lastAnalysisUpdated: null,
  setConn: (c) => set({ conn: c }),
  push: (e) =>
    set((state) => {
      if (e.kind === "_open") return state;
      if (e.kind === "event") {
        const headline =
          (e.payload as { headline?: string } | undefined)?.headline ?? undefined;
        const item: NotificationItem = {
          key: `ev:${e.event_id}`,
          kind: "event",
          symbol: e.symbol,
          event_type: e.event_type,
          event_id: e.event_id,
          watchlisted: e.watchlisted,
          headline,
          receivedAt: Date.now(),
        };
        const items = [item, ...state.items].slice(0, MAX_ITEMS);
        return { items, unread: state.unread + 1 };
      }
      // analysis_updated
      const item: NotificationItem = {
        key: `au:${e.analysis_id}`,
        kind: "analysis_updated",
        symbol: e.symbol,
        event_type: e.event_type,
        event_id: e.event_id,
        analysis_id: e.analysis_id,
        receivedAt: Date.now(),
      };
      const items = [item, ...state.items].slice(0, MAX_ITEMS);
      return {
        items,
        unread: state.unread + 1,
        lastAnalysisUpdated: {
          analysis_id: e.analysis_id,
          symbol: e.symbol,
          event_type: e.event_type,
          at: Date.now(),
        },
      };
    }),
  markAllRead: () => set({ unread: 0 }),
  clear: () => set({ items: [], unread: 0 }),
}));
