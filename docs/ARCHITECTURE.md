# ARCHITECTURE

Single-user MVP. Anthropic models for reasoning, AWS for everything else.

## Diagram (text)

```
                         ┌─────────────────────────────────────┐
                         │            CloudFront               │
                         │  (S3-backed Next.js static export)  │
                         └────────────────┬────────────────────┘
                                          │ HTTPS
                                          ▼
                ┌─────────────────────────────────────────────────┐
                │                    API Gateway                  │
                │   (HTTP API; throttling 100 req/min/IP)         │
                └────────────────────────┬────────────────────────┘
                                         │
                                         ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │                         ECS Fargate                              │
        │   ┌────────────────────────────────────────────────────────┐     │
        │   │  FastAPI (uvicorn)                                     │     │
        │   │   • CorrelationIdMiddleware (echoes X-Correlation-ID)  │     │
        │   │   • RateLimitMiddleware (per-IP token bucket)          │     │
        │   │   • Routes: analyses, chat, watchlist, events,         │     │
        │   │     journal, admin, health                             │     │
        │   │   • Lifespan: agents runtime + EventBroker + LISTEN    │     │
        │   └────────────────────────────────────────────────────────┘     │
        │   ┌────────────────────────────────────────────────────────┐     │
        │   │  In-process LangGraph orchestrator                     │     │
        │   │   • 10 specialists fan out → bull/bear → risk_final →  │     │
        │   │     synthesis → persist                                │     │
        │   │   • Per-analysis Budget hard cap                       │     │
        │   │   • Postgres checkpointer (resume from last node)      │     │
        │   └────────────────────────────────────────────────────────┘     │
        └──────────────┬─────────────────────────┬─────────────────────────┘
                       │                         │
                       ▼                         ▼
   ┌─────────────────────────────┐    ┌─────────────────────────┐
   │ Aurora Postgres Serverless v2│    │  S3 (raw filings,       │
   │  • pgvector HNSW             │    │   reasoning trails)     │
   │  • LISTEN/NOTIFY 'fii_events'│    └─────────────────────────┘
   │  • analyses, outcomes, chat, │
   │    refresh_events, prompts…  │
   └──────────────────────────────┘

External clients (per-provider CircuitBreaker, token-bucket rate limit, retry+jitter):
  Anthropic API · Polygon · FMP · FRED · Finnhub · edgartools (SEC) · Voyage / Bedrock
```

## Why these choices

- **LangGraph in-process, not Step Functions, today**: simpler to iterate on the graph; the
  state TypedDict + checkpointer give us resume-from-checkpoint without external orchestration.
  Step Functions slot opens once the graph stabilizes (Section 0 commitment).
- **Aurora Serverless v2**: per-second billing scales down to ~$0/idle; pgvector HNSW is good
  enough for the few hundred filing-chunk lookups per deep-dive.
- **Anthropic-only models**: prompt caching on the system prompt cuts repeat tool-use loops by
  half. Bedrock fallback for embeddings (Voyage primary) keeps us off OpenAI-shaped APIs.
- **In-process EventBroker (Section 7)**: single API replica today; Redis pub/sub slot opens on
  scale-out. Public surface (`subscribe/publish/emit_event`) doesn't change.
- **Native tool-use loop for the advisor (Section 8)**: cleaner streaming semantics than
  LangGraph for a single conversational agent. Sessions persist in `chat_sessions/messages`.
- **Decision journal (Section 9)**: every action is a tracked prediction; nightly outcomes job
  computes return + alpha vs SPY at 6 horizons. Specialist dominance + prompt-version pivots
  let us A/B prompts on real outcomes.
- **Idempotency + correlation IDs + circuit breakers (Section 10)**: resilience patterns that
  cost almost nothing at this scale and pay back the first time a provider blips.

## State at-rest (key tables)

- `tickers`, `prices_daily`, `prices_intraday`, `fundamentals_quarterly`, `filings`,
  `filing_chunks` (Vector(1024)), `macro_series`, `news_items` (gin index on symbols),
  `insider_transactions`, `institutional_holdings`.
- `analyses` (PK; per-run record) + `analysis_specialist_outputs` (one row per specialist) +
  `analysis_outcomes` (per-horizon returns).
- `agent_prompts` (versioned prompts; `analyses.prompt_versions_json` snapshot).
- `chat_sessions`, `chat_messages`, `user_settings` (cash balance, target concentration).
- `refresh_events` (LISTEN/NOTIFY queue + 15-min debounce on `dedupe_key`).
- LangGraph checkpointer tables (`langgraph_*`) for resume.

## Boundaries

- API enforces auth (single-user today; Cognito wired for later) + rate limit + cost cap.
- Orchestrator enforces per-analysis budget + per-specialist tool-call budgets.
- Tools never compute numbers from prose; specialists never call deterministic math directly.
- News + filing tools wrap untrusted text in sentinel tags; structural injection detector
  flags 20+ canonical payloads (regression test).
