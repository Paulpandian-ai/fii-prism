"""Health endpoints. `/health` is a liveness probe; `/ready` adds a DB round-trip."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app import __version__
from app.db import engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "fii-api", "version": __version__}


@router.get("/ready")
def ready() -> dict[str, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    return {"status": "ready"}
