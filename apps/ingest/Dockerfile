# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY apps/ingest/pyproject.toml apps/ingest/pyproject.toml
COPY packages/agents/pyproject.toml packages/agents/pyproject.toml
COPY packages/db/pyproject.toml packages/db/pyproject.toml
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY packages/data-clients/pyproject.toml packages/data-clients/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --package fii-ingest

COPY apps/ingest apps/ingest
COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package fii-ingest

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app /app

# Default command is the CLI. Fargate scheduled tasks override with e.g.
# ["fii-ingest", "prices-daily", "--ticker", "AAPL"].
ENTRYPOINT ["fii-ingest"]
CMD ["--help"]
