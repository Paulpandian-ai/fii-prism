# syntax=docker/dockerfile:1.7
# Multi-stage image for Fargate. Stage 1 installs deps with uv; stage 2 is the slim runtime.

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /bin/

WORKDIR /app

# Copy workspace manifests for cached dep resolution
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/agents/pyproject.toml packages/agents/pyproject.toml
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY packages/data-clients/pyproject.toml packages/data-clients/pyproject.toml

# Install dependencies (no project yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --package fii-api

# Copy sources and install the project
COPY apps/api apps/api
COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package fii-api

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app/apps/api

COPY --from=builder /app /app

EXPOSE 8000

# Fargate health check hits /health; /ready is the ALB target group check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
