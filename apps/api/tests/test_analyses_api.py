"""Acceptance 1: POST /analyses returns an analysis_id, GET returns the persisted result,
and the list endpoint shows it."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_post_analyses_returns_id(client):
    r = client.post("/analyses", json={"symbol": "AAPL", "analysis_type": "deep_dive"})
    assert r.status_code == 202
    body = r.json()
    assert "analysis_id" in body
    assert body["stream_url"].startswith("/analyses/")
    uuid.UUID(body["analysis_id"])  # valid UUID


def test_get_analysis_and_list(client):
    created = client.post("/analyses", json={"symbol": "AAPL", "analysis_type": "deep_dive"}).json()
    analysis_id = created["analysis_id"]

    # Give the background task a moment to complete. In fake mode the whole graph
    # runs in < 1 second but we're in a sync TestClient so give up to 10s.
    for _ in range(50):
        r = client.get(f"/analyses/{analysis_id}")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            break
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.2))
    else:
        pytest.fail("analysis did not succeed within 10s")

    detail = r.json()
    assert detail["status"] == "succeeded"
    assert detail["symbol"] == "AAPL"
    assert detail["recommendation"] is not None
    assert detail["fii_score"] is not None
    assert len(detail["specialists"]) == 10

    listing = client.get("/analyses", params={"symbol": "AAPL", "limit": 5})
    assert listing.status_code == 200
    assert any(row["analysis_id"] == analysis_id for row in listing.json())


def test_rejects_non_deep_dive(client):
    r = client.post("/analyses", json={"symbol": "AAPL", "analysis_type": "quick_refresh"})
    assert r.status_code == 400
