/**
 * Thin TanStack Query wrappers around the FastAPI backend.
 *
 * Every fetch goes through `apiFetch` so we have one place to add headers
 * (auth tokens, retry policy) when Cognito ships in a later section.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";
import { API_BASE_URL } from "./env";
import type {
  AnalysisDetail,
  AnalysisSummary,
  ChatMessageItem,
  ChatSessionSummary,
  CreateAnalysisRequest,
  CreateAnalysisResponse,
  RefreshEventItem,
  WatchlistEntry,
} from "./types";

// --- Low-level fetch ----------------------------------------------------------------------

async function apiFetch<T>(
  path: string,
  init?: RequestInit & { query?: Record<string, string | number | undefined> },
): Promise<T> {
  const url = new URL(path, API_BASE_URL);
  if (init?.query) {
    for (const [k, v] of Object.entries(init.query)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(url.toString(), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${res.statusText}: ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

// --- Queries ------------------------------------------------------------------------------

export function listAnalyses(opts: { symbol?: string; limit?: number } = {}) {
  return apiFetch<AnalysisSummary[]>("/analyses", {
    query: { symbol: opts.symbol, limit: opts.limit ?? 20 },
  });
}

export function getAnalysis(id: string) {
  return apiFetch<AnalysisDetail>(`/analyses/${id}`);
}

export function createAnalysis(body: CreateAnalysisRequest) {
  return apiFetch<CreateAnalysisResponse>("/analyses", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- Hooks --------------------------------------------------------------------------------

export function useAnalysesList(
  opts: { symbol?: string; limit?: number } = {},
  queryOpts?: Partial<UseQueryOptions<AnalysisSummary[]>>,
) {
  return useQuery({
    queryKey: ["analyses", opts],
    queryFn: () => listAnalyses(opts),
    staleTime: 30_000,
    ...queryOpts,
  });
}

export function useAnalysis(id: string | null | undefined) {
  return useQuery({
    queryKey: ["analyses", id],
    queryFn: () => getAnalysis(id as string),
    enabled: Boolean(id),
    refetchInterval: (q) => {
      const data = q.state.data as AnalysisDetail | undefined;
      // While running, poll every 2s as a fallback if the SSE stream stalls.
      return data?.status === "succeeded" || data?.status === "failed" ? false : 2000;
    },
  });
}

export function useCreateAnalysis() {
  return useMutation({
    mutationFn: createAnalysis,
  });
}

// Stream URL builder for the EventSource client.
export function streamUrl(analysisId: string): string {
  return `${API_BASE_URL.replace(/\/$/, "")}/analyses/${analysisId}/stream`;
}

export function eventsStreamUrl(): string {
  return `${API_BASE_URL.replace(/\/$/, "")}/events/stream`;
}

// --- Watchlist ----------------------------------------------------------------------------

export function listWatchlist() {
  return apiFetch<WatchlistEntry[]>("/watchlist");
}

export function upsertWatchlist(body: { symbol: string; notes?: string | null }) {
  return apiFetch<WatchlistEntry>("/watchlist", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteWatchlist(symbol: string) {
  return apiFetch<void>(`/watchlist/${encodeURIComponent(symbol.toUpperCase())}`, {
    method: "DELETE",
  });
}

export function useWatchlist() {
  return useQuery({
    queryKey: ["watchlist"],
    queryFn: listWatchlist,
    staleTime: 10_000,
  });
}

export function useUpsertWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: upsertWatchlist,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
}

export function useRemoveWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteWatchlist,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
}

// --- Refresh events -----------------------------------------------------------------------

export function listEvents(opts: { symbol?: string; limit?: number } = {}) {
  return apiFetch<RefreshEventItem[]>("/events", {
    query: { symbol: opts.symbol, limit: opts.limit ?? 50 },
  });
}

export function useEventsList(opts: { symbol?: string; limit?: number } = {}) {
  return useQuery({
    queryKey: ["events", opts],
    queryFn: () => listEvents(opts),
    staleTime: 15_000,
  });
}

// --- Chat / advisor -----------------------------------------------------------------------

export function listChatSessions() {
  return apiFetch<ChatSessionSummary[]>("/chat");
}

export function createChatSession() {
  return apiFetch<{ session_id: string }>("/chat", { method: "POST" });
}

export function listChatMessages(sessionId: string) {
  return apiFetch<ChatMessageItem[]>(`/chat/${encodeURIComponent(sessionId)}`);
}

export function deleteChatSession(sessionId: string) {
  return apiFetch<void>(`/chat/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

export function chatStreamUrl(sessionId: string): string {
  return `${API_BASE_URL.replace(/\/$/, "")}/chat/${encodeURIComponent(sessionId)}/message`;
}

export function useChatSessions() {
  return useQuery({
    queryKey: ["chat-sessions"],
    queryFn: listChatSessions,
    staleTime: 5_000,
  });
}

export function useChatMessages(sessionId: string | null) {
  return useQuery({
    queryKey: ["chat-messages", sessionId],
    queryFn: () => listChatMessages(sessionId as string),
    enabled: Boolean(sessionId),
  });
}

export function useCreateChatSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createChatSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chat-sessions"] }),
  });
}

export function useDeleteChatSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteChatSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chat-sessions"] }),
  });
}
