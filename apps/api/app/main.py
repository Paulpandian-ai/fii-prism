"""FastAPI entrypoint. `uvicorn app.main:app --reload` for local dev."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents_runtime import build_runtime
from app.config import get_settings
from app.event_runtime import set_broker
from app.events import EventBroker
from app.listener import shutdown_listener, start_listener_task
from app.logging import configure_logging
from app.routes.analyses import router as analyses_router
from app.routes.chat import router as chat_router
from app.routes.events import router as events_router
from app.routes.health import router as health_router
from app.routes.journal import router as journal_router
from app.routes.watchlist import router as watchlist_router

_settings = get_settings()
configure_logging(_settings.log_level)
log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Initialize the agents runtime once per process (engine, embedder, checkpointer
    # tables, prompt seeding). Failures are logged but don't block boot — read-only
    # endpoints (/health, GET /analyses) should still work.
    runtime = await build_runtime()
    broker = EventBroker()
    set_broker(broker)

    listener_task = None
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(analyses_router)
app.include_router(watchlist_router)
app.include_router(events_router)
app.include_router(chat_router)
app.include_router(journal_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "fii-api", "docs": "/docs"}
