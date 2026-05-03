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
  AdminStats,
  AnalysisDetail,
  AnalysisSummary,
  CachedSpecialistView,
  ChatMessageItem,
  ChatSessionSummary,
  CircuitSnapshot,
  CostCapStatus,
  CreateAnalysisRequest,
  CreateAnalysisResponse,
  CritiqueRow,
  DecisionRow,
  DecisionUpsertRequest,
  JournalBreakdowns,
  JournalSummary,
  RefreshEventItem,
  RunSpecialistResponse,
  SpecialistName,
  SynthesizeBlockedDetail,
  SynthesizeResponse,
  TotalSpendResponse,
  TotalSpendSummary,
  UploadCritiqueResponse,
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

// --- Decision journal --------------------------------------------------------------------

export function upsertDecision(analysisId: string, body: DecisionUpsertRequest) {
  return apiFetch<AnalysisSummary>(
    `/analyses/${encodeURIComponent(analysisId)}/decision`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

export function useUpsertDecision(analysisId: string | null | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DecisionUpsertRequest) =>
      upsertDecision(analysisId as string, body),
    onSuccess: () => {
      if (!analysisId) return;
      qc.invalidateQueries({ queryKey: ["analyses", analysisId] });
      qc.invalidateQueries({ queryKey: ["journal-summary"] });
      qc.invalidateQueries({ queryKey: ["journal-decisions"] });
      qc.invalidateQueries({ queryKey: ["journal-breakdowns"] });
    },
  });
}

export function getJournalSummary() {
  return apiFetch<JournalSummary>("/journal");
}

export function getJournalDecisions(opts: { limit?: number } = {}) {
  return apiFetch<DecisionRow[]>("/journal/decisions", { query: { limit: opts.limit ?? 200 } });
}

export function getJournalBreakdowns() {
  return apiFetch<JournalBreakdowns>("/journal/breakdowns");
}

export function useJournalSummary() {
  return useQuery({
    queryKey: ["journal-summary"],
    queryFn: getJournalSummary,
    staleTime: 30_000,
  });
}

export function useJournalDecisions(opts: { limit?: number } = {}) {
  return useQuery({
    queryKey: ["journal-decisions", opts],
    queryFn: () => getJournalDecisions(opts),
    staleTime: 30_000,
  });
}

export function useJournalBreakdowns() {
  return useQuery({
    queryKey: ["journal-breakdowns"],
    queryFn: getJournalBreakdowns,
    staleTime: 30_000,
  });
}

// --- Admin --------------------------------------------------------------------------------

export function getAdminStats() {
  return apiFetch<AdminStats>("/admin/stats");
}

export function getCircuitBreakers() {
  return apiFetch<CircuitSnapshot[]>("/admin/circuit-breakers");
}

export function getCostCap() {
  return apiFetch<CostCapStatus>("/admin/cost-cap");
}

export function getAdminTotalSpend() {
  return apiFetch<TotalSpendSummary>("/admin/total-spend");
}

// --- Report critiques --------------------------------------------------------------------

export async function uploadCritique(form: FormData): Promise<UploadCritiqueResponse> {
  const url = new URL("/critiques/upload", API_BASE_URL);
  // Don't set Content-Type — the browser sets the multipart boundary correctly.
  const res = await fetch(url.toString(), { method: "POST", body: form });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${res.statusText}: ${text.slice(0, 200)}`);
  }
  return (await res.json()) as UploadCritiqueResponse;
}

export function getCritique(critiqueId: string) {
  return apiFetch<CritiqueRow>(`/critiques/${encodeURIComponent(critiqueId)}`);
}

export function runCritique(critiqueId: string) {
  return apiFetch<CritiqueRow>(`/critiques/${encodeURIComponent(critiqueId)}/run`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function listStockCritiques(symbol: string) {
  return apiFetch<CritiqueRow[]>(
    `/stocks/${encodeURIComponent(symbol.toUpperCase())}/critiques`,
  );
}

export function useCritique(critiqueId: string | null | undefined) {
  return useQuery({
    queryKey: ["critique", critiqueId],
    queryFn: () => getCritique(critiqueId as string),
    enabled: Boolean(critiqueId),
    refetchInterval: (q) => {
      const data = q.state.data as CritiqueRow | undefined;
      // Poll while pending/running so the detail page picks up completion.
      return data && (data.status === "pending" || data.status === "running") ? 2000 : false;
    },
  });
}

export function useRunCritique(critiqueId: string | null | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => runCritique(critiqueId as string),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["critique", critiqueId] }),
  });
}

export function useUploadCritique() {
  return useMutation({ mutationFn: uploadCritique });
}

export function useStockCritiques(symbol: string | null | undefined) {
  return useQuery({
    queryKey: ["stock-critiques", symbol],
    queryFn: () => listStockCritiques(symbol as string),
    enabled: Boolean(symbol),
    staleTime: 30_000,
  });
}

export function useAdminTotalSpend() {
  return useQuery({
    queryKey: ["admin-total-spend"],
    queryFn: getAdminTotalSpend,
    refetchInterval: 30_000,
  });
}

export function useAdminStats() {
  return useQuery({ queryKey: ["admin-stats"], queryFn: getAdminStats, refetchInterval: 30_000 });
}

export function useCircuitBreakers() {
  return useQuery({
    queryKey: ["admin-breakers"],
    queryFn: getCircuitBreakers,
    refetchInterval: 10_000,
  });
}

export function useCostCap() {
  return useQuery({ queryKey: ["admin-cost-cap"], queryFn: getCostCap, refetchInterval: 10_000 });
}

// --- Phase-2 per-specialist endpoints ----------------------------------------------------

/**
 * Thrown by `synthesizeStock` when the backend returns 400 with a structured
 * specialists_not_ready detail. Callers can `instanceof`-check this to render
 * the missing / stale specialist names inline rather than a generic toast.
 */
export class SynthesizeBlockedError extends Error {
  readonly missing: string[];
  readonly stale: string[];
  constructor(detail: SynthesizeBlockedDetail) {
    super(detail.message);
    this.name = "SynthesizeBlockedError";
    this.missing = detail.missing;
    this.stale = detail.stale;
  }
}

export function listStockSpecialists(symbol: string) {
  return apiFetch<CachedSpecialistView[]>(
    `/stocks/${encodeURIComponent(symbol.toUpperCase())}/specialists`,
  );
}

export async function runStockSpecialist(
  symbol: string,
  name: SpecialistName,
  body: { force?: boolean; model_tier?: "sonnet" | "haiku" } = {},
): Promise<RunSpecialistResponse> {
  return apiFetch<RunSpecialistResponse>(
    `/stocks/${encodeURIComponent(symbol.toUpperCase())}/specialists/${encodeURIComponent(name)}`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

/**
 * Synthesize against the symbol's cached specialists. On 400 with the
 * specialists_not_ready detail shape we throw a SynthesizeBlockedError so the
 * UI can render missing/stale lists rather than a flat error string.
 */
export async function synthesizeStock(
  symbol: string,
  body: { use_premium?: boolean } = {},
): Promise<SynthesizeResponse> {
  const url = new URL(
    `/stocks/${encodeURIComponent(symbol.toUpperCase())}/synthesize`,
    API_BASE_URL,
  );
  const res = await fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 400) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail;
    if (detail && typeof detail === "object" && detail.error === "specialists_not_ready") {
      throw new SynthesizeBlockedError(detail as SynthesizeBlockedDetail);
    }
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text.slice(0, 200)}`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${res.statusText}: ${text.slice(0, 200)}`);
  }
  return (await res.json()) as SynthesizeResponse;
}

export function getStockTotalSpend(symbol: string) {
  return apiFetch<TotalSpendResponse>(
    `/stocks/${encodeURIComponent(symbol.toUpperCase())}/total-spend`,
  );
}

// --- Phase-2 hooks -----------------------------------------------------------------------

export function useStockSpecialists(symbol: string | null | undefined) {
  return useQuery({
    queryKey: ["stock-specialists", symbol],
    queryFn: () => listStockSpecialists(symbol as string),
    enabled: Boolean(symbol),
    staleTime: 10_000,
  });
}

export function useRunStockSpecialist(symbol: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      name: SpecialistName;
      force?: boolean;
      model_tier?: "sonnet" | "haiku";
    }) =>
      runStockSpecialist(symbol, vars.name, {
        force: vars.force,
        model_tier: vars.model_tier,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["stock-specialists", symbol] });
      qc.invalidateQueries({ queryKey: ["stock-total-spend", symbol] });
    },
  });
}

export function useSynthesizeStock(symbol: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { use_premium?: boolean } = {}) => synthesizeStock(symbol, vars),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["stock-total-spend", symbol] });
      qc.invalidateQueries({ queryKey: ["analyses"] });
    },
  });
}

export function useStockTotalSpend(symbol: string | null | undefined) {
  return useQuery({
    queryKey: ["stock-total-spend", symbol],
    queryFn: () => getStockTotalSpend(symbol as string),
    enabled: Boolean(symbol),
    staleTime: 10_000,
  });
}
