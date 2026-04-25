# RUNBOOK

What to do when things go wrong. Each scenario lists detection signals + immediate mitigation +
permanent fix.

## Polygon (or FMP / FRED / Finnhub) is down

**Detection**:
- `/admin/circuit-breakers` shows `state: open` for the affected provider.
- `structlog`: `circuit_breaker_opened name=polygon failures=5 cooldown_s=60.0`.
- Web feed shows stale prices; ingestion CLI commands return `UpstreamError ... circuit open`.

**Immediate mitigation**: nothing — the breaker auto-opens, returns cached data via the agents'
graceful-degradation paths (each tool catches `ProviderError` and returns `{"error": "..."}`).
Specialists surface the error in their `auditor_flags` and continue.

**Permanent fix**: when the provider recovers the breaker auto half-opens after 60s and closes
on the first success. If the outage lasts >1h, run `fii-ingest seed -t <symbol>` once recovery
is confirmed to backfill the gap.

## Aurora is slow

**Detection**: pgBouncer connection saturation; FastAPI `/health` returns >1s; `pg_stat_activity`
shows long `idle in transaction`.

**Immediate mitigation**:
1. Restart the API task (drains stuck connections).
2. `aws rds modify-db-cluster --db-cluster-identifier fii-prism-dev \
   --serverless-v2-scaling-configuration MinCapacity=2,MaxCapacity=8` to push more ACUs.

**Permanent fix**: profile the slow query with `EXPLAIN ANALYZE`. Common culprits:
- Missing index on a new query path → add via Alembic migration.
- pgvector HNSW underbuilt for new corpus size → `REINDEX INDEX
  ix_filing_chunks_embedding_hnsw;`.
- Long `idle in transaction` → check for missing `session_scope` close or for the LangGraph
  checkpointer holding a connection (it should be short-lived per-node).

## Costs spike

**Detection**: `/admin/cost-cap` shows `exceeded: true` or `spent_today_usd` ramping fast;
CloudWatch billing alarm fires.

**Immediate mitigation**:
- POST /analyses returns 429 automatically once `FII_DAILY_SPEND_CAP_USD` is hit.
- Lower the per-analysis cap: `FII_BUDGET_USD=0.50` (default $1.00).
- Disable Opus synthesis via the UI toggle (`use_premium_synthesis=False`).

**Permanent fix**:
- Inspect `/admin/stats` `token_usage` table — if a single specialist is dominant, audit its
  prompt for tool-call budget (it should self-cap).
- Check prompt-cache hit rate — system-prompt cache should fire on every repeat call. If not,
  ensure the prompt text is byte-identical run-to-run (no embedded timestamps).
- Consider Bedrock Batch Inference for any nightly bulk scoring (~50% cost vs on-demand).

## An agent returns invalid JSON consistently

**Detection**: `analysis_specialist_outputs.output_json` is empty for that specialist; admin
dashboard's specialist error rate shows the affected one elevated.

**Immediate mitigation**: the orchestrator already retries once with a `ReprompTicket` then
records a `SpecialistError` in state. The analysis still completes (other specialists fill in).

**Permanent fix**: pull a recent failed run from the logs (look for `specialist_real_complete`
events with the failure message), reproduce locally with
`uv run python -c "import asyncio; from fii_agents.specialists.<name> import …"`, then either
tighten the prompt OR widen the schema validator OR add a more permissive coercion in the
specialist. Bump `agent_prompts.version` and roll out via A/B.

## Cognito / auth issues (multi-user mode, post-MVP)

Single-user mode today; once Cognito is wired:
- Failed login → check the Cognito user-pool client config (`AuthFlows = USER_PASSWORD_AUTH`).
- Token rejected by API → the API verifies the JWT using `python-jose`; ensure the JWKS URL
  matches the user-pool region.

## Listener (LISTEN/NOTIFY) doesn't fire

**Detection**: `fii-ingest simulate-shock -t AAPL` succeeds (event_id printed) but no
`analysis_updated` event appears on `/events/stream`.

**Immediate mitigation**:
- Check the listener task is alive: `pg_stat_activity` should show one connection with
  `LISTEN fii_events` as the last query.
- Restart the API.

**Permanent fix**: under `FII_DISABLE_LISTENER=1` the listener is intentionally off (test mode).
Make sure that env var isn't set in prod. The listener auto-reconnects on connection drops with
exponential backoff (1s → 30s).

## Deploy failed

**Detection**: GitHub Actions `Deploy dev` job red.

**Immediate mitigation**:
- `pnpm --filter @fii/infra exec cdk deploy --rollback false` from a dev workstation to see
  the full CFN trace.
- If a stack is in `UPDATE_ROLLBACK_FAILED`, run `aws cloudformation
  continue-update-rollback`.

**Permanent fix**: typically a missing IAM permission on the deploy role, or a CDK construct
that needs `forceUpdateOnReplacement`. The workflow runs `cdk synth` on every PR; if synth
passes locally but deploy fails in CI, the diff is usually credential or region scoped.

## X-Ray distributed tracing (post-MVP)

Not deployed yet. To enable:
1. Add `aws-xray-sdk` to `apps/api/pyproject.toml`.
2. Wire `XRayMiddleware` after `CorrelationIdMiddleware` so the trace ID carries the
   correlation ID as an annotation.
3. Patch `httpx` and `psycopg` so external calls show up as subsegments:
   `xray_recorder.configure(...)` then `patch_all()`.
4. CDK: enable X-Ray on the Fargate task definition's container (`tracing: Tracing.ACTIVE`
   on the task) and grant `AWSXRayDaemonWriteAccess` to the task role.

## 3-month cost forecast (single user, observed pattern)

Assuming current usage of:
- ~5 deep-dive analyses / day at $0.25/run = $1.25/day
- ~10 quick_refresh / day at $0.05/run = $0.50/day
- Advisor chat: ~30 turns/day, average 800 tokens in / 200 tokens out at Sonnet pricing ≈
  $0.36/day
- Embeddings: ~50 chunks/day × 1024 dims × Voyage finance-2 pricing ≈ $0.05/day

| Category                | $/day  | $/30d   |
| ----------------------- | ------ | ------- |
| Deep dives              | $1.25  | $37.50  |
| Quick refreshes         | $0.50  | $15.00  |
| Advisor chat            | $0.36  | $10.80  |
| Embeddings              | $0.05  | $1.50   |
| Aurora Serverless v2 (avg 0.5 ACU) | $1.45 | $43.50 |
| Fargate (1 task, 0.5 vCPU / 1 GB)  | $0.55 | $16.50 |
| S3 / CloudFront / data egress      | $0.30 | $9.00  |
| **Total / month**       |        | **~$134** |
| 3-month forecast        |        | **~$402** |

Hard cap (FII_DAILY_SPEND_CAP_USD=20) covers ~16x the expected daily LLM spend; budget alarms
fire well before the cap.
