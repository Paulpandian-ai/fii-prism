import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";

import type { CachedSpecialistView, SpecialistName } from "@/lib/types";

/**
 * Build a fresh QueryClient per test so cache state doesn't leak between
 * cases. retry: false is critical — a flaky transient error in a test would
 * otherwise spin for several seconds.
 */
export function newQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

export function renderWithQuery(ui: ReactNode, client: QueryClient = newQueryClient()) {
  return {
    client,
    ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>),
  };
}

/**
 * Build a CachedSpecialistView shaped like what GET /stocks/{sym}/specialists
 * actually returns. Defaults are "missing"; override per-test as needed.
 */
export function makeView(
  name: SpecialistName,
  overrides: Partial<CachedSpecialistView> = {},
): CachedSpecialistView {
  return {
    name,
    state: "missing",
    status: null,
    last_run_at: null,
    expires_at: null,
    cost_usd: 0,
    has_output: false,
    output: null,
    ...overrides,
  };
}
