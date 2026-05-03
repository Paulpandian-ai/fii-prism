"""Acceptance 1: POST /analyses returns an analysis_id, GET returns the persisted result,
and the list endpoint shows it."""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _seed_ticker(symbol: str) -> None:
    """Pre-create the ticker so the analyses.symbol FK is satisfied at insert time
    (load_context only upserts after the BG task starts, which races with the FK check)."""
    from app.agents_runtime import get_runtime
    from fii_db import Ticker
    from fii_db.session import session_scope
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    factory = get_runtime().session_factory
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )


def test_post_analyses_returns_id(client):
    r = client.post("/analyses", json={"symbol": "AAPL", "analysis_type": "deep_dive"})
    assert r.status_code == 202
    body = r.json()
    assert "analysis_id" in body
    assert body["stream_url"].startswith("/analyses/")
    uuid.UUID(body["analysis_id"])  # valid UUID


def test_get_analysis_and_list(client):
    # Unique per-test symbol so the per-minute idempotency_key dedupe doesn't fold this
    # run into a prior test's analysis_id. Letters-only suffix avoids tripping the
    # NumericClaimsMixin regex inside RiskOutput.qualitative_summary on substrings
    # like "8B" / "1F" interpreted as uncited numeric claims.
    symbol = "ZZL" + ("".join(c for c in uuid.uuid4().hex.upper() if c.isalpha()) + "AAAA")[:4]
    _seed_ticker(symbol)
    created = client.post("/analyses", json={"symbol": symbol, "analysis_type": "deep_dive"}).json()
    analysis_id = created["analysis_id"]

    # Give the background task a moment to complete. The GET call itself drives the
    # TestClient's internal event loop, so we just need to poll with a small sync
    # sleep — never call asyncio.run / run_until_complete inside a TestClient context
    # since it conflicts with the loop driving lifespan + listener tasks.
    for _ in range(50):
        r = client.get(f"/analyses/{analysis_id}")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            break
        time.sleep(0.2)
    else:
        pytest.fail("analysis did not succeed within 10s")

    detail = r.json()
    assert detail["status"] == "succeeded"
    assert detail["symbol"] == symbol
    assert detail["recommendation"] is not None
    assert detail["fii_score"] is not None
    assert len(detail["specialists"]) == 10

    listing = client.get("/analyses", params={"symbol": symbol, "limit": 5})
    assert listing.status_code == 200
    assert any(row["analysis_id"] == analysis_id for row in listing.json())


def test_rejects_unknown_analysis_type(client):
    r = client.post("/analyses", json={"symbol": "AAPL", "analysis_type": "stress_test"})
    assert r.status_code == 400
