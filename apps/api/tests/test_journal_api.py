"""Section 9: PATCH decision + GET /journal aggregations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from fii_db import (
    Analysis,
    AnalysisOutcome,
    AnalysisRecommendation,
    AnalysisSpecialistOutput,
    AnalysisStatus,
    AnalysisType,
    Confidence,
    Ticker,
)
from fii_db.session import session_scope
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _seed_journaled_with_outcome(
    session_factory,
    *,
    recommendation: str,
    alpha_1m: float,
    hit_1m: bool,
    dominance: str,
    confidence: str = "high",
) -> str:
    aid = str(uuid.uuid4())
    sym = f"ZZJ{aid[:4].upper()}"
    started = datetime.now(UTC) - timedelta(days=35)
    with session_scope(session_factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=sym, name=sym)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )
        s.execute(
            pg_insert(Analysis).values(
                analysis_id=aid,
                symbol=sym,
                analysis_type=AnalysisType.DEEP_DIVE.value,
                status=AnalysisStatus.SUCCEEDED.value,
                initiated_at=started,
                completed_at=started,
                recommendation=recommendation,
                confidence=confidence,
                fii_score=Decimal("7.0"),
                action_taken="bought",
                action_size_usd=Decimal("1000"),
                action_price=Decimal("100"),
                action_at=started,
                prompt_versions_json={"fundamentals": 1, "valuation": 1},
            )
        )
        s.execute(
            pg_insert(AnalysisSpecialistOutput).values(
                analysis_id=aid,
                specialist_name="bull",
                output_json={"confidence": "high"},
            )
        )
        s.execute(
            pg_insert(AnalysisOutcome).values(
                analysis_id=aid,
                symbol=sym,
                entry_price=Decimal("100"),
                entry_date=started.date(),
                return_1m=Decimal("0.10"),
                alpha_1m=Decimal(str(alpha_1m)),
                hit_1m=hit_1m,
                dominance=dominance,
                spy_available=True,
            )
        )
    return aid


def _cleanup(factory, analysis_id: str) -> None:
    with session_scope(factory) as s:
        s.execute(delete(AnalysisOutcome).where(AnalysisOutcome.analysis_id == analysis_id))
        s.execute(
            delete(AnalysisSpecialistOutput).where(
                AnalysisSpecialistOutput.analysis_id == analysis_id
            )
        )
        s.execute(delete(Analysis).where(Analysis.analysis_id == analysis_id))


def test_patch_decision_persists_action(client):
    # Unique per-test symbol so the per-minute idempotency_key dedupe doesn't reuse
    # a prior test's analysis_id (which may already have an action recorded).
    from app.agents_runtime import get_runtime
    from fii_db import Ticker
    from fii_db.session import session_scope
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    symbol = f"ZZD{uuid.uuid4().hex[:6].upper()}"
    factory = get_runtime().session_factory
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )

    created = client.post("/analyses", json={"symbol": symbol, "analysis_type": "deep_dive"}).json()
    aid = created["analysis_id"]
    import time

    for _ in range(50):
        r = client.get(f"/analyses/{aid}")
        if r.status_code == 200 and r.json().get("status") == "succeeded":
            break
        time.sleep(0.2)
    else:
        pytest.fail("analysis did not succeed within 10s")

    # Initially action_taken == 'none'.
    body = r.json()
    assert body["action_taken"] == "none"

    patch = client.patch(
        f"/analyses/{aid}/decision",
        json={
            "action_taken": "bought",
            "action_size_usd": 2500,
            "action_price": 175.5,
            "action_notes": "Long-term hold; valuation discount sufficient.",
        },
    )
    assert patch.status_code == 200
    out = patch.json()
    assert out["action_taken"] == "bought"
    assert out["action_size_usd"] == 2500
    assert out["action_at"] is not None

    # GET shows the persisted action.
    refreshed = client.get(f"/analyses/{aid}").json()
    assert refreshed["action_taken"] == "bought"
    assert refreshed["action_notes"].startswith("Long-term hold")


def test_journal_summary_aggregates(client):
    from app.agents_runtime import get_runtime

    factory = get_runtime().session_factory
    a1 = _seed_journaled_with_outcome(
        factory,
        recommendation=AnalysisRecommendation.BUY.value,
        alpha_1m=0.08,
        hit_1m=True,
        dominance="bull",
    )
    a2 = _seed_journaled_with_outcome(
        factory,
        recommendation=AnalysisRecommendation.HOLD.value,
        alpha_1m=-0.02,
        hit_1m=True,
        dominance="balanced",
        confidence=Confidence.MEDIUM.value,
    )
    a3 = _seed_journaled_with_outcome(
        factory,
        recommendation=AnalysisRecommendation.BUY.value,
        alpha_1m=-0.05,
        hit_1m=False,
        dominance="bull",
    )

    try:
        r = client.get("/journal")
        assert r.status_code == 200
        body = r.json()
        assert body["decision_count"] >= 3
        assert body["hit_rate_by_horizon"]["1m"] is not None
        # We seeded 2 hits and 1 miss out of (>=3) → hit-rate ≥ 0
        assert body["hit_rate_by_horizon"]["1m"] >= 0.5

        decisions = client.get("/journal/decisions", params={"limit": 50}).json()
        ids = {d["analysis_id"] for d in decisions}
        assert {a1, a2, a3}.issubset(ids)

        breakdowns = client.get("/journal/breakdowns").json()
        rec_buckets = {b["label"]: b for b in breakdowns["by_recommendation"]}
        # buy bucket should aggregate over a1+a3
        assert "buy" in rec_buckets
        assert rec_buckets["buy"]["count"] >= 2
        dom_buckets = {b["label"]: b for b in breakdowns["by_dominance"]}
        assert "bull" in dom_buckets
        # by_prompt_version exposes a key with our seeded versions.
        pv_buckets = breakdowns["by_prompt_version"]
        assert any("fund=1" in b["label"] for b in pv_buckets)
    finally:
        for aid in (a1, a2, a3):
            _cleanup(factory, aid)
