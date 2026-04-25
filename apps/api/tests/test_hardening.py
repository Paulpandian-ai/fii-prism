"""Section 10: idempotency, daily cap, correlation ID echo, rate limit."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_idempotency_key_is_stable_within_minute():
    from app.routes.analyses import _idempotency_key

    a = _idempotency_key("AAPL", "deep_dive")
    b = _idempotency_key("AAPL", "deep_dive")
    assert a == b


def test_idempotency_key_differs_across_symbols():
    from app.routes.analyses import _idempotency_key

    assert _idempotency_key("AAPL", "deep_dive") != _idempotency_key("MSFT", "deep_dive")
    assert _idempotency_key("AAPL", "deep_dive") != _idempotency_key("AAPL", "quick_refresh")


def test_correlation_id_is_echoed_back(client):
    cid = "test-correlation-1234"
    r = client.get("/health", headers={"X-Correlation-ID": cid})
    assert r.status_code == 200
    assert r.headers.get("X-Correlation-ID") == cid


def test_correlation_id_generated_when_missing(client):
    r = client.get("/health")
    assert r.status_code == 200
    cid = r.headers.get("X-Correlation-ID")
    assert cid and len(cid) >= 32  # uuid4-shaped


def test_admin_cost_cap_endpoint_returns_status(client):
    r = client.get("/admin/cost-cap")
    assert r.status_code == 200
    body = r.json()
    for k in ("cap_usd", "spent_today_usd", "remaining_usd", "exceeded"):
        assert k in body
    assert isinstance(body["exceeded"], bool)


def test_admin_circuit_breakers_endpoint(client):
    r = client.get("/admin/circuit-breakers")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_admin_stats_endpoint_shape(client):
    r = client.get("/admin/stats")
    assert r.status_code == 200
    body = r.json()
    assert "daily_volume" in body
    assert "specialist_health" in body
    assert "token_usage" in body


def test_daily_cap_blocks_new_analyses(monkeypatch, client):
    # Force the cap to $0 so any spend already on the books triggers the rejection.
    # We pre-seed by inserting a small synthetic analysis with cost > 0.
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.agents_runtime import get_runtime
    from fii_db import Analysis, AnalysisStatus, AnalysisType, Ticker
    from fii_db.session import session_scope
    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    factory = get_runtime().session_factory
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol="ZZCAP", name="ZZCAP")
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )
        cap_breaker_id = "00000000-0000-0000-0000-cccccccccccc"
        s.execute(delete(Analysis).where(Analysis.analysis_id == cap_breaker_id))
        s.execute(
            pg_insert(Analysis).values(
                analysis_id=cap_breaker_id,
                symbol="ZZCAP",
                analysis_type=AnalysisType.DEEP_DIVE.value,
                status=AnalysisStatus.SUCCEEDED.value,
                initiated_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                total_cost_usd=Decimal("100.0"),
            )
        )
    monkeypatch.setenv("FII_DAILY_SPEND_CAP_USD", "1.0")

    try:
        r = client.post("/analyses", json={"symbol": "ZZCAP2", "analysis_type": "deep_dive"})
        assert r.status_code == 429
        assert "daily spend cap" in r.json()["detail"]
    finally:
        with session_scope(factory) as s:
            s.execute(
                delete(Analysis).where(
                    Analysis.analysis_id == "00000000-0000-0000-0000-cccccccccccc"
                )
            )
