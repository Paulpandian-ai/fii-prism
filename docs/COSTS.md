# FII-PRISM — Cost Estimate (Section 2 scope)

Monthly spend for the **dev** environment at MVP scale: one user (you), one watchlist
ticker actively ingesting (AAPL), plus the default macro series. All figures in USD,
us-east-1 list prices (2025-Q4). Costs **exclude** agent inference (Section 3+).

## Aurora PostgreSQL Serverless v2

| Item | Assumption | Monthly |
|------|-----------|---------|
| ACU-hours (idle 0.5 ACU × 24h × 30d) | Low OLTP; bursts to 2–4 ACU for seed jobs | ~$43 |
| Burst allowance (5 ACU-hours/day avg) | Seeds + analysis spikes | ~$16 |
| Storage (10 GB) | Seed data + ~1 GB/month filing chunks | ~$1 |
| I/O (Aurora I/O-Optimized NOT enabled; pay-per-IO at dev scale) | ~1M IOs/month | ~$2 |
| **Aurora subtotal** | | **~$62** |

**Lever if this is too high:** switch to `db.t4g.medium` RDS Postgres (not Serverless v2)
for ~$30/mo flat. Serverless v2 wins as soon as you have multiple analyses/day.

## Fargate — FastAPI + scheduled ingest jobs

| Item | Assumption | Monthly |
|------|-----------|---------|
| API service (1 task × 0.5 vCPU × 1 GB × 24h × 30d) | Always-on | ~$15 |
| Ingest scheduled tasks (seed-aapl only, 1 run/day × ~5 min × 1 vCPU × 2 GB) | 150 task-min/mo | ~$0.30 |
| NAT gateway egress (~5 GB filings + 2 GB news/month) | | ~$7 |
| **Fargate + egress subtotal** | | **~$22** |

Activating all ingestion schedules (prices-daily, macro, news-15m, insiders) bumps the
Fargate line to ~$8/mo and adds ~$2/mo NAT egress.

## S3

| Item | Assumption | Monthly |
|------|-----------|---------|
| Reasoning-trails bucket (1 GB) | ~30 analyses × 30 MB each | ~$0.025 |
| Raw-data bucket (2 GB filings as text) | 10-K ≈ 1 MB per filing × 500 filings cap | ~$0.05 |
| Requests | | <$0.10 |
| **S3 subtotal** | | **~$0.20** |

## Networking

| Item | Assumption | Monthly |
|------|-----------|---------|
| NAT gateway (1 AZ) — hourly | 730 h × $0.045 | ~$33 |
| CloudFront (10 GB delivered) | Mostly cache hits after warm | ~$1 |
| Route 53 / API Gateway HTTP API | Trivial until traffic grows | ~$1 |
| **Networking subtotal** | | **~$35** |

If NAT hurts: S3/Bedrock/Secrets Manager VPC endpoints would remove most NAT egress,
trading ~$33 NAT for ~$15 in endpoint hours. Worth doing once Section 3 wires Bedrock.

## Data providers (external, paid by you)

| Provider | Tier | Monthly |
|----------|------|---------|
| Polygon.io | Starter | $29 |
| Financial Modeling Prep | Starter (w/ student discount) | $22–29 |
| Finnhub | Free | $0 |
| FRED | Free | $0 |
| SEC EDGAR via edgartools | Free | $0 |
| Voyage AI (voyage-finance-2) | PAYG ($0.12/1M tokens) | ≤$2 at MVP scale |
| Bedrock Titan Embed v2 (fallback) | PAYG ($0.02/1M tokens) | ~$0 until Voyage fails |
| **Provider subtotal** | | **$53–60** |

## Roll-up

| Group | Monthly |
|-------|---------|
| Aurora | ~$62 |
| Fargate + egress | ~$22 |
| Networking (primarily NAT) | ~$35 |
| S3 + misc | ~$1 |
| **AWS total (dev)** | **~$120** |
| Data providers | ~$55 |
| **Grand total (dev)** | **~$175** |

## Variance notes

- **NAT dominates idle AWS cost.** If we want to hit the $60–80/mo target from Section 0,
  a one-shot simplification (replace NAT with VPC endpoints for S3 + Bedrock + Secrets
  Manager, then drop the NAT gateway, serving only outbound provider traffic through
  the public ALB) takes the networking line from ~$35 to ~$8. Estimate post-optimization:
  ~$90/mo AWS + $55/mo providers = **~$145/mo grand total**.
- **Agent inference** lands in Section 3. Budget ≤ $0.50 per full deep-dive (Section 0).
  At 1 deep-dive/day that's an additional ~$15/mo on top of these numbers.
- **Intraday prices** are disabled at MVP. Re-enabling them (15-min cadence × market
  hours) adds ~$1/mo in Fargate and ~$1/mo in NAT egress — cheap.

## How this was measured

These numbers come from AWS's published list prices and back-of-envelope assumptions
for MVP usage. Refine once CloudWatch has 30 days of real data by running:

```sh
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics "UnblendedCost" \
  --group-by Type=TAG,Key=Project
```
