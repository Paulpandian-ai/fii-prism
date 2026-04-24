"""FastAPI entrypoint. `uvicorn app.main:app --reload` for local dev."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.logging import configure_logging
from app.routes.health import router as health_router

_settings = get_settings()
configure_logging(_settings.log_level)

app = FastAPI(
    title="FII-PRISM API",
    version="0.1.0",
    description=(
        "FII-PRISM backend. Sync reads and orchestrator triggers. "
        "Agent runs execute in Step Functions, not here."
    ),
)

# CORS: allow the Next.js dev origin; production origin list comes from settings later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "fii-api", "docs": "/docs"}
