/**
 * Mounts once at the app layout and keeps an EventSource open on /events/stream
 * for the lifetime of the tab. Parsed envelopes feed the events store; the
 * browser's built-in EventSource handles auto-reconnect.
 */

"use client";

import { useEffect } from "react";
import { eventsStreamUrl } from "./api";
import { useEventsStore } from "./events-store";
import type { BrokerEnvelope } from "./types";

export function useEventsStream() {
  const push = useEventsStore((s) => s.push);
  const setConn = useEventsStore((s) => s.setConn);

  useEffect(() => {
    setConn("connecting");
    const es = new EventSource(eventsStreamUrl());
    es.onopen = () => setConn("open");
    es.onerror = () => {
      setConn(es.readyState === EventSource.CONNECTING ? "reconnecting" : "error");
    };
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as BrokerEnvelope;
        push(data);
      } catch {
        // ignore malformed frames; heartbeats are comments so they don't arrive here
      }
    };
    return () => {
      es.close();
      setConn("closed");
    };
  }, [push, setConn]);
}
