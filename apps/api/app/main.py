"""FastAPI entrypoint. `uvicorn app.main:app --reload` for local dev."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env / .env.local from the repo root BEFORE any application imports so the
# agent code in packages/agents/src/fii_agents/model.py — which reads
# os.environ.get("ANTHROPIC_API_KEY") directly at Model.__init__ — sees the key.
# pydantic-settings loads the file into the Settings instance but does not export
# values back into os.environ; this load_dotenv does. override=False so a real
# environment variable (e.g. set by ECS task definition in prod) wins over the file.
_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_ROOT / ".env.local", override=False)
load_dotenv(_REPO_ROOT / ".env", override=False)

import structlog  # noqa: E402  (import after load_dotenv is intentional)
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.agents_runtime import build_runtime  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.event_runtime import set_broker  # noqa: E402
from app.events import EventBroker  # noqa: E402
from app.listener import shutdown_listener, start_listener_task  # noqa: E402
from app.logging import configure_logging  # noqa: E402
from app.middleware.correlation import CorrelationIdMiddleware  # noqa: E402
from app.middleware.rate_limit import RateLimitMiddleware  # noqa: E402
from app.routes.admin import router as admin_router  # noqa: E402
from app.routes.analyses import router as analyses_router  # noqa: E402
from app.routes.chat import router as chat_router  # noqa: E402
from app.routes.events import router as events_router  # noqa: E402
from app.routes.health import router as health_router  # noqa: E402
from app.routes.journal import router as journal_router  # noqa: E402
from app.routes.stocks import router as stocks_router  # noqa: E402
from app.routes.watchlist import router as watchlist_router  # noqa: E402

_settings = get_settings()
configure_logging(_settings.log_level)
log = structlog.get_logger(__name__)
# Single, no-secret startup line so we can confirm at a glance whether real-LLM
# specialists will run. Logs the boolean only — never the key value.
log.info(
    "anthropic_key_status",
    configured=bool(os.environ.get("ANTHROPIC_API_KEY")),
    fake_mode_forced=os.environ.get("FII_USE_FAKE_MODEL") == "1",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Initialize the agents runtime once per process (engine, embedder, checkpointer
    # tables, prompt seeding). Failures are logged but don't block boot — read-only
    # endpoints (/health, GET /analyses) should still work.
    runtime = await build_runtime()
    broker = EventBroker()
    set_broker(broker)

    # Skip the LISTEN/NOTIFY listener under FII_DISABLE_LISTENER (tests). The listener
    # holds a long-lived async psycopg connection that doesn't always cancel cleanly
    # when TestClient teardown rebuilds the lifespan, and tests don't exercise it.
    listener_task = None
    if not _disable_listener():
        try:
            listener_task = start_listener_task(runtime, broker)
        except Exception:
            log.exception("listener_start_failed")

    try:
        yield
    finally:
        if listener_task is not None:
            await shutdown_listener(listener_task)
        # Cancel any in-flight background analyses started via POST /analyses or the
        # advisor's run_quick tool — without this, tests that rebuild the lifespan
        # leave dead tasks attached to a closed event loop, which deadlocks the next
        # TestClient.
        await _cancel_background_tasks()


def _disable_listener() -> bool:
    import os

    return os.environ.get("FII_DISABLE_LISTENER", "").lower() in ("1", "true", "yes")


async def _cancel_background_tasks() -> None:
    import asyncio
    import contextlib

    from app.routes.analyses import _BACKGROUND_TASKS as ANALYSES_TASKS

    pending = [t for t in ANALYSES_TASKS if not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        with contextlib.suppress(asyncio.CancelledError, Exception, asyncio.TimeoutError):
            await asyncio.wait(pending, timeout=2.0)
    ANALYSES_TASKS.clear()


app = FastAPI(
    title="FII-PRISM API",
    version="0.1.0",
    description=(
        "FII-PRISM backend. Kicks off deep-dive analyses and streams their progress. "
        "LangGraph orchestration runs in-process today; moves to Step Functions later."
    ),
    lifespan=lifespan,
)

# Order matters with add_middleware — the LAST one added runs first on the way in.
# Correlation ID outermost so every other middleware (and downstream logs) sees it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-ID"],
)
app.add_middleware(RateLimitMiddleware, per_minute=100)
app.add_middleware(CorrelationIdMiddleware)

app.include_router(health_router)
app.include_router(analyses_router)
app.include_router(stocks_router)
app.include_router(watchlist_router)
app.include_router(events_router)
app.include_router(chat_router)
app.include_router(journal_router)
app.include_router(admin_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "fii-api", "docs": "/docs"}
