/**
 * EventSource hook for the live-analysis stream.
 *
 * The browser's EventSource handles auto-reconnection natively. On disconnect
 * the underlying server reads from the LangGraph checkpoint table, so the
 * resumed stream replays from the last persisted node — no work is lost.
 *
 * The hook applies each event to the Zustand store. Components subscribe to
 * the store; this hook just owns the connection lifecycle.
 */

"use client";

import { useEffect } from "react";
import { streamUrl } from "./api";
import { useLiveStore } from "./store";
import type { NodeEvent } from "./types";

export function useAnalysisStream(analysisId: string | null) {
  const setConn = useLiveStore((s) => s.setConn);
  const applyEvent = useLiveStore((s) => s.applyEvent);
  const setAnalysis = useLiveStore((s) => s.setAnalysis);

  useEffect(() => {
    if (!analysisId) return;
    setAnalysis(analysisId);
    setConn("connecting");

    const es = new EventSource(streamUrl(analysisId));

    es.onopen = () => setConn("open");
    es.onerror = () => {
      // Browser will auto-reconnect; reflect that in the UI.
      setConn(es.readyState === EventSource.CONNECTING ? "reconnecting" : "error");
    };
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as NodeEvent;
        applyEvent(data);
        if (data.node === "_end") setConn("closed");
      } catch {
        // ignore malformed payloads
      }
    };

    return () => {
      es.close();
      setConn("closed");
    };
  }, [analysisId, setAnalysis, setConn, applyEvent]);
}
