# FII-PRISM

**Factor Impact Intelligence — v2 (web).** Multi-agent research and decision-support for
long-horizon fundamental investing. Anthropic + AWS only. Educational use only; not investment
advice.

MVP scope (Section 0 decisions): **Deep-Dive + Portfolio + Feed + Stress Testing**. Single-user.
Step Functions orchestrates deep-dive runs. Aurora Postgres (pgvector) is the primary datastore.
S3 holds full reasoning trails. Frontend is a Next.js static export on S3 + CloudFront.

---

## Repo layout

```
apps/
  web/                 Next.js 16 (App Router, React 19, Tailwind v4) — static export
  api/                 FastAPI backend (Fargate) + Alembic migrations
packages/
  agents/              LangGraph orchestrator + specialists (Python)
  shared/              Pydantic schemas + mirrored zod types
  data-clients/        Polygon / FMP / edgartools / FRED / Finnhub wrappers
infra/                 AWS CDK v2 (TypeScript) — 6 stacks
  lib/network-stack.ts
  lib/data-stack.ts    Aurora Serverless v2 + S3 buckets
  lib/auth-stack.ts    Cognito user pool
  lib/compute-stack.ts ECS Fargate cluster + ALB-fronted service
  lib/api-stack.ts     API Gateway HTTP API → ALB
  lib/frontend-stack.ts S3 + CloudFront for the web static export
.github/workflows/     CI (lint/typecheck/synth) + Deploy dev
docker-compose.yml     Local Postgres 16 with pgvector
```

## Prerequisites

| Tool        | Version | Install                                                              |
| ----------- | ------- | -------------------------------------------------------------------- |
| Node        | 22+     | `nvm install 22`                                                     |
| pnpm        | 10+     | `corepack enable && corepack prepare pnpm@10 --activate`             |
| Python      | 3.11    | `uv python install 3.11`                                             |
| uv          | 0.8+    | `curl -LsSf https://astral.sh/uv/install.sh \| sh`                   |
| Docker      | 24+     | Docker Desktop or equivalent                                         |
| AWS CLI v2  | 2.x     | https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html |
| AWS CDK     | 2.175+  | Installed as a workspace devDep; use via `pnpm --filter @fii/infra …` |

AWS credentials: configure `AWS_PROFILE=fii-dev` (or set `AWS_ACCESS_KEY_ID` etc.) before
running any `deploy:dev` commands.

## First-run setup

```bash
# 1. Install Node deps (workspace-wide)
pnpm install

# 2. Install Python deps (workspace-wide)
uv sync --all-packages --all-extras

# 3. Copy env template
cp .env.example .env.local
# then fill in ANTHROPIC_API_KEY and any provider keys you already have
```

## Local dev

```bash
# Terminal 1 — Postgres (pgvector preinstalled, extensions enabled on init)
docker compose up -d postgres

# Terminal 2 — run migrations (one-off; rerun when migrations change)
cd apps/api
uv run alembic upgrade head

# Terminal 3 — FastAPI
cd apps/api
uv run uvicorn app.main:app --reload
# -> http://localhost:8000, /docs for OpenAPI, /health and /ready for probes

# Terminal 4 — Next.js
pnpm --filter @fii/web dev
# -> http://localhost:3000
```

## Deploy

```bash
# One-time per AWS account/region
pnpm --filter @fii/infra exec cdk bootstrap

# Synth (dry run) — produces CloudFormation for all 6 stacks
pnpm cdk:synth

# Deploy all stacks to dev
pnpm deploy:dev
```

CI deploys to dev automatically on merge to `main`. Required GitHub Actions variables/secrets:

| Kind    | Name                        | Purpose                                       |
| ------- | --------------------------- | --------------------------------------------- |
| secret  | `AWS_DEPLOY_ROLE_ARN`       | IAM role the workflow assumes via OIDC        |
| var     | `WEB_BUCKET_NAME`           | S3 bucket from `FrontendStack` output        |
| var     | `WEB_DISTRIBUTION_ID`       | CloudFront distribution ID from the same stack |
| var     | `NEXT_PUBLIC_API_BASE_URL`  | HTTP API URL from `ApiStack` output           |

## Environment variables

All of the following are read at runtime. Local values go in `.env.local`; production values
are pulled from AWS Secrets Manager by the Fargate task role.

| Name                          | Consumer       | Required for         | Notes                                          |
| ----------------------------- | -------------- | -------------------- | ---------------------------------------------- |
| `DATABASE_URL`                | API, Alembic   | Local + prod         | `postgresql+psycopg://…`                       |
| `ANTHROPIC_API_KEY`           | API, agents    | Dev only             | Prod uses Bedrock via IAM                      |
| `AWS_REGION`                  | API, agents    | Prod                 | Defaults to `us-east-1`                        |
| `POLYGON_API_KEY`             | data-clients   | Section 2+           | Not yet used                                   |
| `FMP_API_KEY`                 | data-clients   | Section 2+           | Not yet used                                   |
| `FINNHUB_API_KEY`             | data-clients   | Section 2+           | Not yet used                                   |
| `FRED_API_KEY`                | data-clients   | Section 2+           | Not yet used                                   |
| `SEC_CONTACT_EMAIL`           | data-clients   | **Ingest pipeline**  | **Required.** Real, deliverable email — see below |
| `EDGAR_IDENTITY`              | data-clients   | Optional override    | If set, takes precedence over `SEC_CONTACT_EMAIL` |
| `NEXT_PUBLIC_API_BASE_URL`    | web            | Local + prod         | Baked into the static export at build time     |
| `NEXT_PUBLIC_APP_ENV`         | web            | Local + prod         | `development` \| `dev` \| `prod`               |
| `LOG_LEVEL`                   | API            | Always               | `debug` \| `info` \| `warn` \| `error`         |
| `CDK_DEFAULT_ACCOUNT`         | cdk (deploy)   | Deploy               | From `aws sts get-caller-identity`             |
| `CDK_DEFAULT_REGION`          | cdk (deploy)   | Deploy               | Defaults to `us-east-1`                        |

### `SEC_CONTACT_EMAIL` is required for the ingest pipeline

SEC EDGAR's compliance policy requires every API client to identify itself with a
real, deliverable email. We send `User-Agent: FII-PRISM research <SEC_CONTACT_EMAIL>`.
**`@example.com` addresses are silently throttled — in practice SEC returns HTTP 403
and every filing/insider request fails.** That's how the seed pipeline ended up with
`filings: 0, insiders: 0` for AAPL despite `edgar_call duration_ms=11669` looking
healthy in logs.

The `EdgarClient` constructor refuses to start without a real email:

```bash
# .env.local
SEC_CONTACT_EMAIL=research@yourdomain.com
```

If you need to override the full identity string (e.g., to match a corporate naming
convention), set `EDGAR_IDENTITY` directly — the constructor leaves it untouched.

## Production hardening (Section 10)

| Concern               | Where it lives                                                                    |
| --------------------- | --------------------------------------------------------------------------------- |
| Idempotency           | `analyses.idempotency_key` (sha256 of symbol+type+minute), reused for 1h           |
| Daily spend cap       | `apps/api/app/cost_gate.py` (POST /analyses returns 429 when over `FII_DAILY_SPEND_CAP_USD`) |
| Per-analysis cap      | `fii_agents.Budget` — short-circuits remaining specialists once `cost_running_total ≥ cap` |
| Circuit breakers      | `fii_shared.CircuitBreaker` per data-client provider (5 fails → 60s open)         |
| Retry with jitter     | `fii_shared.retry_with_jitter` (AWS full-jitter), tenacity in data-client base    |
| Correlation IDs       | `app.middleware.correlation` — `X-Correlation-ID` echoed + threaded to structlog  |
| Rate limit            | `app.middleware.rate_limit` — per-IP token bucket (100/min default)               |
| Prompt injection      | `_INJECTION_PATTERNS` in news specialist + 20-payload regression test             |
| Admin observability   | `GET /admin/{stats,circuit-breakers,cost-cap}` + `/admin` web dashboard           |
| Secret hygiene        | `.gitleaks.toml` + `.pre-commit-config.yaml` block any commit with high-entropy strings |

See `docs/RUNBOOK.md` for incident playbooks and `docs/AGENTS.md` for per-specialist details.

## Data-provider plan limits

### FMP Starter ($22/mo with student discount)

FMP returns **HTTP 402 Payment Required** for endpoints or parameters above your
plan tier. The client degrades gracefully — every endpoint logs a structured warning
and returns the empty value of its return type rather than raising. The seed and
agent loops continue with whatever data is available.

| Endpoint | Starter availability | Soft-miss behavior |
| --- | --- | --- |
| `/stable/profile` | ✅ allowed | (n/a) |
| `/stable/income-statement?period=annual\|quarter` | ✅ allowed | logs `fmp_income_statement_not_in_plan`, returns `[]` |
| `/stable/balance-sheet-statement` | ✅ allowed | logs `fmp_balance_sheet_not_in_plan`, returns `[]` |
| `/stable/cash-flow-statement` | ✅ allowed | logs `fmp_cash_flow_not_in_plan`, returns `[]` |
| `/stable/ratios?period=annual` | ✅ allowed | logs `fmp_ratios_blocked_skipping`, returns `[]` |
| `/stable/ratios?period=quarter` | ❌ premium | **Auto-falls back to annual** + logs `fmp_ratios_quarter_blocked_falling_back_to_annual` |
| `/stable/analyst-estimates` | ❌ premium | logs `fmp_analyst_estimates_not_in_plan`, returns `[]` |
| `/stable/discounted-cash-flow-valuation` | ❌ premium | logs `fmp_dcf_not_in_plan`, returns `None` (the Valuation specialist falls back to its own DCF math via the `run_dcf` tool) |

If you upgrade to a higher FMP tier, no code changes are needed — the methods will
simply start receiving 200s where they previously got 402s.

## Documentation index

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system diagram, rationale, trade-offs.
- [`docs/AGENTS.md`](docs/AGENTS.md) — one section per specialist (tools, prompt, cost, failure modes).
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — what to do when Polygon is down, Aurora is slow, costs spike, etc.
- [`docs/DISCLAIMERS.md`](docs/DISCLAIMERS.md) — full educational-purpose language and limits.
- [`docs/COSTS.md`](docs/COSTS.md) — model cost table + per-analysis budget breakdown.

Every AI output in this app ends with: _"For educational purposes only. Not investment
advice."_
