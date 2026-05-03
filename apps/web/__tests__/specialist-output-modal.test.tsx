import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";

import { SpecialistCell } from "@/components/stock/specialist-cell";

import { makeView, renderWithQuery } from "./test-utils";

describe("SpecialistOutputModal — opens with cached output", () => {
  it("renders the qualitative summary, citations, and footer when View output is clicked", async () => {
    const user = userEvent.setup();
    const view = makeView("fundamentals", {
      state: "fresh",
      status: "ok",
      last_run_at: "2026-05-01T09:00:00Z",
      expires_at: "2026-05-31T09:00:00Z",
      cost_usd: 0.18,
      has_output: true,
      output: {
        symbol: "AAPL",
        confidence: "medium",
        qualitative_summary:
          "AAPL shows stable margins and healthy cash conversion across the trailing four quarters.",
        revenue_ttm: {
          value: 391_000_000_000,
          unit: "USD",
          as_of: "2026-04-30",
          source: {
            source_type: "fmp_fundamental",
            source_id: "fmp/aggregate/AAPL",
            section: null,
            retrieved_at: "2026-05-01T09:00:00Z",
            url: null,
          },
        },
        gross_margin_trend: "stable",
        auditor_flags: [
          {
            claim: "10-K not ingested; analysis based on FMP statements only.",
            confidence: "medium",
            sources: [
              {
                source_type: "calculated",
                source_id: "filings/missing",
                section: null,
                retrieved_at: "2026-05-01T09:00:00Z",
                url: null,
              },
            ],
          },
        ],
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

    // Click "View output" to open the modal.
    await user.click(screen.getByTestId("view-output-fundamentals"));

    // The dialog title shows the display name + symbol.
    const dialog = await screen.findByTestId("output-modal-fundamentals");
    expect(dialog).toBeInTheDocument();
    expect(dialog.textContent).toMatch(/Fundamentals/);
    expect(dialog.textContent).toMatch(/AAPL/);

    // Qualitative summary section is rendered.
    expect(
      screen.getByText(/AAPL shows stable margins/i),
    ).toBeInTheDocument();

    // Cited number rendered (heuristic: the popover trigger shows the formatted value).
    expect(screen.getByRole("heading", { name: /Numbers/i })).toBeInTheDocument();

    // Auditor flag rendered as a CitedClaim entry.
    expect(screen.getByRole("heading", { name: /Auditor Flags/i })).toBeInTheDocument();
    expect(
      screen.getByText(/10-K not ingested; analysis based on FMP statements only/i),
    ).toBeInTheDocument();

    // Footer carries cost + last run.
    expect(dialog.textContent).toMatch(/cost \$0\.18/);
  });
});
