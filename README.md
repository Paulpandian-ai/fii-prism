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
| `NEXT_PUBLIC_API_BASE_URL`    | web            | Local + prod         | Baked into the static export at build time     |
| `NEXT_PUBLIC_APP_ENV`         | web            | Local + prod         | `development` \| `dev` \| `prod`               |
| `LOG_LEVEL`                   | API            | Always               | `debug` \| `info` \| `warn` \| `error`         |
| `CDK_DEFAULT_ACCOUNT`         | cdk (deploy)   | Deploy               | From `aws sts get-caller-identity`             |
| `CDK_DEFAULT_REGION`          | cdk (deploy)   | Deploy               | Defaults to `us-east-1`                        |

## What this section delivers (and deliberately does not)

**Delivers:** empty-but-wired monorepo; Next.js home-page shell with v1 color palette; FastAPI
with `/health` and `/ready`; Postgres + pgvector locally via compose; Alembic scaffold with
initial extension migration; six CDK stacks that synth cleanly.

**Does not deliver yet:** agents, LLM calls, data ingestion, auth UI, charts, any real tables
beyond the Alembic bootstrap, or a Step Functions orchestrator. Those land in Sections 2+.

Every AI output in this app will end with: _"For educational purposes only. Not investment
advice."_
