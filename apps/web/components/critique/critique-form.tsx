"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useUploadCritique } from "@/lib/api";

const MAX_PDF_BYTES = 25 * 1024 * 1024;

const REPORT_SOURCES: { value: string; label: string }[] = [
  { value: "morningstar", label: "Morningstar" },
  { value: "seeking_alpha", label: "Seeking Alpha" },
  { value: "sell_side", label: "Sell-side research" },
  { value: "other", label: "Other" },
];

interface Props {
  /** Pre-fill the symbol field; useful when launching from /stock/?s=XYZ. */
  initialSymbol?: string;
}

/**
 * Upload form used by the /critique landing page. Validates the file client-side
 * (PDF only, <=25MB) then posts to /critiques/upload and navigates to the
 * critique detail page on success.
 */
export function CritiqueForm({ initialSymbol }: Props) {
  const router = useRouter();
  const upload = useUploadCritique();

  const [symbol, setSymbol] = useState(initialSymbol ?? "");
  const [reportSource, setReportSource] = useState<string>("morningstar");
  const [pdf, setPdf] = useState<File | null>(null);
  const [validation, setValidation] = useState<string | null>(null);

  function handleFile(f: File | null) {
    setValidation(null);
    if (!f) {
      setPdf(null);
      return;
    }
    const isPdf =
      f.type === "application/pdf" ||
      f.type === "application/x-pdf" ||
      f.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) {
      setValidation("Upload must be a PDF (.pdf).");
      setPdf(null);
      return;
    }
    if (f.size > MAX_PDF_BYTES) {
      setValidation(
        `File is ${(f.size / 1024 / 1024).toFixed(1)}MB; max ${MAX_PDF_BYTES / 1024 / 1024}MB.`,
      );
      setPdf(null);
      return;
    }
    setPdf(f);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setValidation(null);
    const sym = symbol.trim().toUpperCase();
    if (!sym) {
      setValidation("Symbol is required.");
      return;
    }
    if (!pdf) {
      setValidation("Attach a PDF report.");
      return;
    }
    const form = new FormData();
    form.append("symbol", sym);
    form.append("report_source", reportSource);
    form.append("pdf", pdf);
    upload.mutate(form, {
      onSuccess: (data) => {
        router.push(`/critique/?id=${encodeURIComponent(data.critique_id)}`);
      },
    });
  }

  const apiError = upload.error
    ? (upload.error as Error).message
    : null;

  return (
    <Card data-testid="critique-form">
      <CardHeader>
        <CardTitle>Critique an analyst report</CardTitle>
        <CardDescription>
          Upload a Morningstar / Seeking Alpha / sell-side PDF. We&apos;ll compare its claims
          against our specialist analysis for the same ticker.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-sm font-medium text-fii-navy">
              Ticker
              <Input
                aria-label="ticker"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="AAPL"
                maxLength={10}
                className="mt-1 uppercase"
                data-testid="critique-ticker-input"
              />
            </label>
            <label className="text-sm font-medium text-fii-navy">
              Report source
              <select
                aria-label="report source"
                value={reportSource}
                onChange={(e) => setReportSource(e.target.value)}
                className="mt-1 block h-10 w-full rounded-md border border-fii-navy-100 bg-white px-3 text-sm text-fii-navy"
                data-testid="critique-source-select"
              >
                {REPORT_SOURCES.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="block text-sm font-medium text-fii-navy">
            PDF (max 25MB)
            <input
              type="file"
              accept="application/pdf,.pdf"
              aria-label="pdf upload"
              onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
              className="mt-1 block w-full text-sm text-fii-ink file:mr-3 file:rounded-md file:border-0 file:bg-fii-navy-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-fii-navy hover:file:bg-fii-navy-100"
              data-testid="critique-file-input"
            />
            {pdf && (
              <span className="mt-1 block text-xs text-fii-mute">
                {pdf.name} · {(pdf.size / 1024).toFixed(0)} KB
              </span>
            )}
          </label>

          {validation && (
            <p
              role="alert"
              className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
              data-testid="critique-validation-error"
            >
              {validation}
            </p>
          )}
          {apiError && !validation && (
            <p
              role="alert"
              className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
            >
              {apiError}
            </p>
          )}

          <Button
            type="submit"
            disabled={upload.isPending || !pdf || !symbol.trim()}
            data-testid="critique-submit"
          >
            {upload.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Uploading…
              </>
            ) : (
              <>Upload and analyze</>
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
