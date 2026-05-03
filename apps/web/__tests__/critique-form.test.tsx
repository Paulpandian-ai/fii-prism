import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

import { CritiqueForm } from "@/components/critique/critique-form";

import { renderWithQuery } from "./test-utils";

/**
 * Emulate a file selection on a hidden <input type="file">. We use fireEvent
 * directly because userEvent.upload enforces the `accept` attribute (which
 * would reject .txt before our component validator gets to run, defeating
 * the test's purpose).
 */
function uploadFile(input: HTMLInputElement, file: File) {
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  fireEvent.change(input);
}

describe("CritiqueForm — file validation", () => {
  it("rejects a non-PDF file with a structured error", () => {
    renderWithQuery(<CritiqueForm initialSymbol="AAPL" />);
    const input = screen.getByTestId("critique-file-input") as HTMLInputElement;
    const wrong = new File(["not a pdf"], "report.txt", { type: "text/plain" });
    uploadFile(input, wrong);

    const err = screen.getByTestId("critique-validation-error");
    expect(err.textContent ?? "").toMatch(/must be a PDF/i);
    // Submit stays disabled because no valid PDF is selected.
    expect(screen.getByTestId("critique-submit")).toBeDisabled();
  });

  it("rejects oversize files (>25MB) with a structured error", () => {
    renderWithQuery(<CritiqueForm initialSymbol="AAPL" />);
    const input = screen.getByTestId("critique-file-input") as HTMLInputElement;
    // jsdom honors the buffer length when reporting File.size, so a 26MB Uint8Array
    // crosses the 25MB cap.
    const big = new File([new Uint8Array(26 * 1024 * 1024)], "big.pdf", {
      type: "application/pdf",
    });
    uploadFile(input, big);

    const err = screen.getByTestId("critique-validation-error");
    expect(err.textContent ?? "").toMatch(/max 25MB/i);
  });

  it("accepts a valid small PDF and enables the submit button", () => {
    renderWithQuery(<CritiqueForm initialSymbol="AAPL" />);
    const input = screen.getByTestId("critique-file-input") as HTMLInputElement;
    const ok = new File([new Uint8Array(2048)], "report.pdf", {
      type: "application/pdf",
    });
    uploadFile(input, ok);

    expect(screen.queryByTestId("critique-validation-error")).toBeNull();
    expect(screen.getByTestId("critique-submit")).not.toBeDisabled();
  });
});
