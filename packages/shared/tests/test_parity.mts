#!/usr/bin/env tsx
/*
 * Parity test: validate the same JSON fixture the Pydantic suite consumes, using the
 * zod schemas. Any drift between Python and TS definitions fails this test.
 *
 * Run: `pnpm --filter @fii/shared test` (wired through package.json `test` script).
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  BullBearDebateOutput,
  FundamentalsOutput,
  InsiderFlowOutput,
  MacroOutput,
  MoatOutput,
  NewsSentimentOutput,
  OrchestratorFinalOutput,
  RiskOutput,
  TechnicalOutput,
  ValuationOutput,
} from "../src/schemas";
import type { ZodTypeAny } from "zod";

const here = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(here, "fixtures", "valid_outputs.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as Record<string, unknown>;

const CASES: Array<[string, ZodTypeAny]> = [
  ["FundamentalsOutput", FundamentalsOutput],
  ["ValuationOutput", ValuationOutput],
  ["MoatOutput", MoatOutput],
  ["MacroOutput", MacroOutput],
  ["TechnicalOutput", TechnicalOutput],
  ["NewsSentimentOutput", NewsSentimentOutput],
  ["InsiderFlowOutput", InsiderFlowOutput],
  ["RiskOutput", RiskOutput],
  ["BullBearDebateOutput", BullBearDebateOutput],
  ["OrchestratorFinalOutput", OrchestratorFinalOutput],
];

let failed = 0;
for (const [name, schema] of CASES) {
  const payload = fixture[name];
  if (payload == null) {
    console.error(`FAIL ${name}: missing fixture entry`);
    failed += 1;
    continue;
  }
  const result = schema.safeParse(payload);
  if (!result.success) {
    console.error(`FAIL ${name}:`);
    for (const issue of result.error.issues) {
      console.error(`  - ${issue.path.join(".")}: ${issue.message}`);
    }
    failed += 1;
  } else {
    console.log(`PASS ${name}`);
  }
}

// Negative test: null source on revenue_ttm must be rejected.
{
  const copy = structuredClone(fixture.FundamentalsOutput as Record<string, unknown>);
  (copy.revenue_ttm as Record<string, unknown>).source = null;
  const result = FundamentalsOutput.safeParse(copy);
  if (result.success) {
    console.error("FAIL negative-test: null revenue_ttm.source was accepted");
    failed += 1;
  } else {
    console.log("PASS negative-test: null revenue_ttm.source rejected");
  }
}

// Negative test: fewer than 3 items in what_could_make_me_wrong must be rejected.
{
  const copy = structuredClone(
    fixture.OrchestratorFinalOutput as Record<string, unknown>,
  );
  (copy as { what_could_make_me_wrong: unknown[] }).what_could_make_me_wrong =
    (copy as { what_could_make_me_wrong: unknown[] }).what_could_make_me_wrong.slice(
      0,
      2,
    );
  const result = OrchestratorFinalOutput.safeParse(copy);
  if (result.success) {
    console.error("FAIL negative-test: what_could_make_me_wrong < 3 was accepted");
    failed += 1;
  } else {
    console.log("PASS negative-test: what_could_make_me_wrong < 3 rejected");
  }
}

if (failed > 0) {
  console.error(`\n${failed} parity failures`);
  process.exit(1);
}
console.log("\nAll zod parity checks passed.");
