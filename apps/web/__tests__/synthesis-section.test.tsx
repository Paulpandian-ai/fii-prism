import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

import { SynthesisSection } from "@/components/stock/synthesis-section";
import { SPECIALIST_ORDER } from "@/lib/types";

import { makeView, renderWithQuery } from "./test-utils";

describe("SynthesisSection — readiness gating", () => {
  it("disables the synthesize button and lists missing specialists when one is missing", () => {
    // 7 specialists fresh, "risk" missing.
    const views = SPECIALIST_ORDER.map((name) =>
      name === "risk"
        ? makeView(name)
        : makeView(name, {
            state: "fresh",
            status: "ok",
            last_run_at: "2026-05-01T12:00:00Z",
            expires_at: "2026-05-31T12:00:00Z",
            cost_usd: 0.1,
            has_output: true,
            output: {},
          }),
    );

    renderWithQuery(<SynthesisSection symbol="AAPL" views={views} />);

    const button = screen.getByTestId("synthesize-button");
    expect(button).toBeDisabled();
    expect(button.getAttribute("aria-label")).toBe("Synthesis not ready");

    const blockers = screen.getByTestId("synthesis-blockers");
    expect(blockers.textContent).toMatch(/1 specialist not ready/);
    expect(blockers.textContent).toMatch(/Risk/);
  });

  it("enables synthesize when every specialist is fresh + ok", () => {
    const views = SPECIALIST_ORDER.map((name) =>
      makeView(name, {
        state: "fresh",
        status: "ok",
        last_run_at: "2026-05-01T12:00:00Z",
        expires_at: "2026-05-31T12:00:00Z",
        cost_usd: 0.05,
        has_output: true,
        output: {},
      }),
    );

    renderWithQuery(<SynthesisSection symbol="AAPL" views={views} />);

    const button = screen.getByTestId("synthesize-button");
    expect(button).not.toBeDisabled();
    expect(screen.queryByTestId("synthesis-blockers")).toBeNull();
  });
});
