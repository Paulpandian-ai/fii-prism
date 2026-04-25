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
from app.routes.events import router as events_router
from app.routes.health import router as health_router
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


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "fii-api", "docs": "/docs"}
