/**
 * Thin TanStack Query wrappers around the FastAPI backend.
 *
 * Every fetch goes through `apiFetch` so we have one place to add headers
 * (auth tokens, retry policy) when Cognito ships in a later section.
 */

import {
  useMutation,
  useQuery,
  type UseQueryOptions,
} from "@tanstack/react-query";
import { API_BASE_URL } from "./env";
import type {
  AnalysisDetail,
  AnalysisSummary,
  CreateAnalysisRequest,
  CreateAnalysisResponse,
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
