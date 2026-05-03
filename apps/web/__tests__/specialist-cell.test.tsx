import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { SpecialistCell } from "@/components/stock/specialist-cell";

import { makeView, renderWithQuery } from "./test-utils";

describe("SpecialistCell — missing state", () => {
  it("shows the no-analysis-yet placeholder and a Run button", () => {
    const view = makeView("fundamentals"); // defaults to state=missing
    renderWithQuery(
      <SpecialistCell
        view={view}
        symbol="AAPL"
        onRun={vi.fn()}
        isRunning={false}
        error={null}
      />,
    );

    expect(screen.getByRole("heading", { name: "Fundamentals" })).toBeInTheDocument();
    expect(screen.getByText(/no analysis yet/i)).toBeInTheDocument();

    const runBtn = screen.getByTestId("run-button-fundamentals");
    // Missing → label is "Run", not "Re-run".
    expect(runBtn.textContent ?? "").toMatch(/Run/);
    expect(runBtn.textContent ?? "").not.toMatch(/Re-run/);

    // No cached output → View output is disabled.
    expect(screen.getByTestId("view-output-fundamentals")).toBeDisabled();

    // Status badge shows "missing" with the neutral variant.
    const badge = screen.getByTestId("specialist-badge-fundamentals");
    expect(badge.textContent).toBe("missing");
  });
});

describe("SpecialistCell — stale state", () => {
  it("renders the cached summary, the warning badge, and a Re-run label", () => {
    const view = makeView("news", {
      state: "stale",
      status: "ok",
      last_run_at: "2026-04-20T12:00:00Z",
      expires_at: "2026-04-20T16:00:00Z",
      cost_usd: 0.04,
      has_output: true,
      output: {
        net_sentiment: -0.12,
        articles_analyzed: 30,
        qualitative_summary: "30 articles inspected.",
        confidence: "medium",
      },
    });

    renderWithQuery(
      <SpecialistCell
        view={view}
        symbol="AAPL"
        onRun={vi.fn()}
        isRunning={false}
        error={null}
      />,
    );

    // The badge surface shows "stale" — and the cell carries the warning
    // (amber) variant via the badge's bg/text classes (asserted via class).
    const badge = screen.getByTestId("specialist-badge-news");
    expect(badge.textContent).toBe("stale");
    expect(badge.className).toMatch(/amber/);

    // Cached summary renders the net sentiment + article count.
    expect(screen.getByText(/net -0.12/)).toBeInTheDocument();
    expect(screen.getByText(/30 articles/)).toBeInTheDocument();

    // Re-run label (since last_run_at is non-null).
    const runBtn = screen.getByTestId("run-button-news");
    expect(runBtn.textContent ?? "").toMatch(/Re-run/);

    // View output is enabled because has_output=true.
    expect(screen.getByTestId("view-output-news")).not.toBeDisabled();
  });
});
