# AGENTS

One section per specialist. Tools, prompt version, expected cost, and known failure modes.
Prompt text lives in `packages/agents/src/fii_agents/prompts.py` (DB-versioned via
`agent_prompts`).

| Specialist     | Model        | Tools (max calls)            | Typical cost / run | Notes |
| -------------- | ------------ | ---------------------------- | ------------------ | ----- |
| fundamentals   | Sonnet 4.6   | 6 read tools (15)            | $0.03 – $0.06      | Reads 10-K via RAG; wraps text in `<filing_text>` tags |
| valuation      | Sonnet 4.6   | DCF + WACC + reverse (12)    | $0.04 – $0.08      | Three DCFs (bear/base/bull) + sensitivity table |
| moat           | Sonnet 4.6   | RAG queries (8)              | $0.02 – $0.05      | Adversarial: 3 erosion queries before bull case |
| macro          | Sonnet 4.6   | FRED series + regime (10)    | $0.02 – $0.04      | Rate trajectory, regime classification |
| technical      | Sonnet 4.6   | get_indicators (4)           | $0.01 – $0.02      | Pre-computed indicators only; never math |
| news_sentiment | Sonnet 4.6   | get_news + read_8k (8)       | $0.02 – $0.04      | Untrusted-content wrap + injection detector |
| insider_flow   | Sonnet 4.6   | form4 + cluster (8)          | $0.02 – $0.03      | Distinguishes 10b5-1 programmatic from discretionary |
| risk           | Sonnet 4.6   | dfast + corr + drawdown (10) | $0.03 – $0.05      | Runs twice (preliminary + final after debate) |
| bull           | Sonnet 4.6   | none                         | $0.02 – $0.03      | Reframes upstream evidence; no new claims |
| bear           | Sonnet 4.6   | none                         | $0.02 – $0.03      | Same, adversarial |
| synthesis      | Sonnet 4.6 / Opus 4.7 | none                | $0.02 – $0.10      | Opus when `use_premium_synthesis=True`; otherwise Sonnet |

Total per deep-dive: ~$0.25 (Sonnet) or ~$0.60 (with Opus synthesis). Budget hard-cap default
is $1.00/run via `Budget.from_env()`.

## Specialist details

### fundamentals

- **Job**: extract income / balance / cash flow / ratios + read latest 10-K Item 1 / 1A / 7.
- **Tools**: `get_income_statement`, `get_balance_sheet`, `get_cash_flow`, `get_ratios`,
  `get_filings_list`, `read_filing_section`, `query_filing_rag`,
  `calculate_net_debt_to_ebitda`.
- **Hard rule**: every CitedNumber wraps source ref; never compute numbers in prose.
- **Failure modes**: missing 10-K (no SEC filings ingested) → degrades to ratios-only;
  fundamentals JSON validation fails → retry once with re-prompt then surface error.

### valuation

- **Job**: bear/base/bull DCF + sensitivity + reverse DCF.
- **Tools**: `get_balance_sheet`, `get_cash_flow`, `get_ratios`, `get_risk_free_rate_latest`,
  `get_beta`, `calculate_wacc`, `run_dcf`, `solve_implied_growth`.
- **Hard rule**: wacc > terminal_growth always; ERP defaults to 5.5%.
- **Failure modes**: insufficient FCF history → DCF tool returns `error`; specialist surfaces
  the error in `auditor_flags` and continues with comparable-multiples only.

### moat

- **Job**: durability assessment using Porter Five Forces + ROIC trajectory.
- **Tools**: `read_filing_section`, `query_filing_rag`, `get_ratios`.
- **Hard rule**: 3 erosion queries before any bull-case observation (`evidence_against` non-empty).
- **Failure modes**: thin 10-K → both `evidence_for` and `evidence_against` are short → mark
  `confidence=low` and let synthesis weight accordingly.

### macro

- **Job**: regime classification + rate trajectory + sector sensitivity.
- **Tools**: `classify_regime`, `get_yield_curve`, `get_fred_latest`, `get_fred_trailing`.
- **Hard rule**: at least 3 FRED series cited in `regime_evidence`.

### technical

- **Job**: trend / signal / suggested entry/stop/target from pre-computed indicators.
- **Tools**: `get_indicators` (returns SMA20/50/200, RSI, MACD, ADX, ATR, S/R), `get_recent_closes`.
- **Hard rule**: never compute indicators in prose.

### news_sentiment

- **Job**: net sentiment + top themes + earnings guidance changes.
- **Tools**: `get_recent_news`, `read_8k`, `read_transcript_excerpt`.
- **Hard rule**: untrusted news wrapped in `<untrusted_news_content>` tags; injection attempts
  surfaced in `anomaly_flags`. The structural detector regression test (Section 10) covers 20
  canonical payloads.

### insider_flow

- **Job**: programmatic-vs-discretionary classification, cluster activity, 13F context.
- **Tools**: `get_form4_transactions`, `detect_cluster_activity`, `get_13f_holdings`.
- **Hard rule**: 10b5-1 plans counted as noise unless concentrated.

### risk

- **Job**: position sizing + hard stop + DFAST scenarios; runs twice.
- **Tools**: `calculate_dfast_scenarios`, `get_avg_daily_volume`, `get_correlation_to_benchmark`,
  `get_max_drawdown_historical`.
- **Hard rule**: `position_size_rec_pct` is a plain float, not CitedNumber.

### bull / bear

- **Job**: argue the case using only evidence already produced upstream.
- **Tools**: none.
- **Hard rule**: every CitedClaim's source must already appear in an upstream specialist's output.

### synthesis

- **Job**: weighted final recommendation + thesis + what-could-make-me-wrong.
- **Tools**: none.
- **Hard rule**: `disclaimer == "For educational purposes only. Not investment advice."` —
  enforced by `OrchestratorFinalOutput` validator.

## Prompt versioning

`agent_prompts` table is the source of truth. Each row is `(specialist_name, version, model,
prompt_text, is_active, notes)`. Defaults are bundled in code (`DEFAULT_PROMPTS`) and seeded on
startup. Per-run snapshot of versions lives in `analyses.prompt_versions_json` so the journal
can pivot decisions across prompt-bundle versions (`/journal/breakdowns?by=prompt_version`).

To roll out a new prompt version:

```sql
INSERT INTO agent_prompts (specialist_name, version, model, prompt_text, is_active, notes)
VALUES ('fundamentals', 2, 'claude-sonnet-4-6', '...new text...', false, 'tightened citations');
-- A/B by activating only on a subset of runs (manual today; scheduled rollout post-MVP):
UPDATE agent_prompts SET is_active = true  WHERE specialist_name='fundamentals' AND version=2;
UPDATE agent_prompts SET is_active = false WHERE specialist_name='fundamentals' AND version=1;
```

The journal shows `fund=1,...` vs `fund=2,...` buckets; once you have ≥10 decisions per
bucket, rollback or promote based on hit-rate / alpha differential.
