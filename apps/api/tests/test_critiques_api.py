"""Section: analyst-report critique API.

Covers:
- Upload dedupe by sha256 (same PDF twice -> same critique_id).
- Upload rejects unknown ticker with 404 + ticker_not_found body.
- Upload rejects non-PDF (415) and oversize (413).
- Run produces a persisted ok row with non-zero cost when the critic returns
  a real result (mocked Claude — no API key needed in tests).
- gaps_in_analysis references the missing/stale specialists when the cache
  doesn't contain them.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _seed_ticker(symbol: str) -> None:
    from app.agents_runtime import get_runtime
    from fii_db import Ticker
    from fii_db.session import session_scope
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    factory = get_runtime().session_factory
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol.upper(), name=symbol.upper())
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )


def _wipe_critiques(symbol: str) -> None:
    from app.agents_runtime import get_runtime
    from fii_db import ReportCritique
    from fii_db.session import session_scope

    factory = get_runtime().session_factory
    with session_scope(factory) as s:
        s.execute(delete(ReportCritique).where(ReportCritique.symbol == symbol.upper()))


# Minimal valid-ish PDF body — enough for the upload path; nothing actually
# parses it in fake mode. Bytes start with %PDF-1.4 so any naive sniffing is happy.
_FAKE_PDF = b"%PDF-1.4\n%\xc3\xa4\nfake critique fixture body\n"


# --- 1. Upload dedupe -------------------------------------------------------------------


def test_upload_same_pdf_twice_returns_same_critique_id(client):
    sym = "TSTCR1"
    _seed_ticker(sym)
    _wipe_critiques(sym)

    files = {"pdf": ("report.pdf", io.BytesIO(_FAKE_PDF), "application/pdf")}
    data = {"symbol": sym, "report_source": "morningstar"}
    r1 = client.post("/critiques/upload", data=data, files=files)
    assert r1.status_code == 201, r1.text
    payload1 = r1.json()
    assert payload1["status"] == "pending"
    assert payload1["deduped"] is False
    cid = payload1["critique_id"]

    # Re-post the same bytes — same hash, must return the same row.
    files2 = {"pdf": ("different-name.pdf", io.BytesIO(_FAKE_PDF), "application/pdf")}
    r2 = client.post("/critiques/upload", data=data, files=files2)
    assert r2.status_code == 201, r2.text
    payload2 = r2.json()
    assert payload2["critique_id"] == cid
    assert payload2["deduped"] is True

    _wipe_critiques(sym)


# --- 2. Ticker not found -----------------------------------------------------------------


def test_upload_unknown_ticker_returns_ticker_not_found(client):
    files = {"pdf": ("r.pdf", io.BytesIO(_FAKE_PDF), "application/pdf")}
    data = {"symbol": "ZZNOPE", "report_source": "morningstar"}
    r = client.post("/critiques/upload", data=data, files=files)
    assert r.status_code == 404
    body = r.json()
    detail = body.get("detail")
    # FastAPI wraps dict-detail under `detail`. We look for the structured shape.
    assert isinstance(detail, dict)
    assert detail.get("error") == "ticker_not_found"
    assert detail.get("symbol") == "ZZNOPE"


# --- 3. Non-PDF + oversize rejection ----------------------------------------------------


def test_upload_rejects_non_pdf(client):
    sym = "TSTCR2"
    _seed_ticker(sym)
    _wipe_critiques(sym)
    files = {"pdf": ("report.txt", io.BytesIO(b"not a pdf"), "text/plain")}
    data = {"symbol": sym, "report_source": "morningstar"}
    r = client.post("/critiques/upload", data=data, files=files)
    assert r.status_code == 415


def test_upload_rejects_oversize(client):
    sym = "TSTCR3"
    _seed_ticker(sym)
    _wipe_critiques(sym)
    # 26 MB > 25 MB cap.
    big = b"%PDF-1.4\n" + (b"x" * (26 * 1024 * 1024))
    files = {"pdf": ("big.pdf", io.BytesIO(big), "application/pdf")}
    data = {"symbol": sym, "report_source": "morningstar"}
    r = client.post("/critiques/upload", data=data, files=files)
    assert r.status_code == 413


# --- 4. End-to-end run with mocked critic -----------------------------------------------


def test_run_persists_ok_row_with_nonzero_cost(client, monkeypatch):
    """We monkeypatch run_report_critique to simulate the two-call success
    path so the test exercises persistence + the GET endpoints without
    needing an Anthropic key."""
    from fii_agents.specialists.report_critic import ReportCriticResult

    sym = "TSTCR4"
    _seed_ticker(sym)
    _wipe_critiques(sym)

    files = {"pdf": ("ms.pdf", io.BytesIO(_FAKE_PDF + b"4"), "application/pdf")}
    data = {"symbol": sym, "report_source": "morningstar"}
    r = client.post("/critiques/upload", data=data, files=files)
    assert r.status_code == 201
    cid = r.json()["critique_id"]

    async def _fake_runner(**kwargs):
        return ReportCriticResult(
            status="ok",
            extracted_claims={
                "symbol": kwargs["symbol"],
                "report_source": kwargs["report_source"],
                "analyst_name": "Jane Analyst",
                "publication_date": None,
                "recommendation": "Buy",
                "price_target": None,
                "time_horizon": "12 months",
                "bull_case_summary": "Strong moat, accelerating revenue.",
                "bear_case_summary": "Multiple compression risk.",
                "key_numerical_claims": [],
                "key_qualitative_claims": [],
                "stated_assumptions": [],
                "analyst_disclosures": [],
            },
            critique={
                "critique_id": kwargs["critique_id"],
                "symbol": kwargs["symbol"],
                "report_source": kwargs["report_source"],
                "analyst_name": "Jane Analyst",
                "numerical_accuracy": [],
                "logical_strengths": [],
                "logical_weaknesses": [],
                "unstated_assumptions": [],
                "bias_indicators": [],
                "gaps_in_analysis": [],
                "reliability_rating": "medium",
                "reliability_rationale": "Reasonable but not deeply sourced.",
                "one_line_verdict": "Solid framing; verify the moat claim.",
                "cost_usd": 0.18,
            },
            cost_usd=0.18,
            tokens_in=4500,
            tokens_out=1100,
            duration_ms=12_000,
            model_used="claude-sonnet-4-6",
        )

    # Patch the actual import location used inside the route.
    monkeypatch.setattr("app.routes.critiques.run_report_critique", _fake_runner)

    r2 = client.post(f"/critiques/{cid}/run", json={})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "ok"
    assert body["cost_usd"] == pytest.approx(0.18, rel=1e-3)
    assert body["tokens_in"] == 4500
    assert body["one_line_verdict"] == "Solid framing; verify the moat claim."
    assert body["critique"]["reliability_rating"] == "medium"

    # GET /critiques/{id} returns the same payload.
    r3 = client.get(f"/critiques/{cid}")
    assert r3.status_code == 200
    assert r3.json()["status"] == "ok"

    # GET /stocks/{sym}/critiques lists this row most-recent-first.
    r4 = client.get(f"/stocks/{sym}/critiques")
    assert r4.status_code == 200
    rows = r4.json()
    assert any(r["critique_id"] == cid for r in rows)

    # Idempotent: re-running an ok row returns the same body without re-calling
    # the (still patched) runner.
    monkeypatch.setattr(
        "app.routes.critiques.run_report_critique",
        _raise_called,
    )
    r5 = client.post(f"/critiques/{cid}/run", json={})
    assert r5.status_code == 200
    assert r5.json()["status"] == "ok"

    _wipe_critiques(sym)


async def _raise_called(**_):  # pragma: no cover — only invoked if idempotency breaks
    raise AssertionError("run_report_critique called for already-ok row")


# --- 5. gaps_in_analysis surfaces missing specialists -----------------------------------


def test_gaps_in_analysis_references_missing_specialists(monkeypatch):
    """Integration: when run_report_critique builds critic messages, missing
    specialists must show up in the unavailable_specialists block so the
    prompt can wire them into gaps_in_analysis. We assert the message-build
    helper sees the missing names from the cache lookup."""
    from app.agents_runtime import get_runtime
    from fii_agents.specialists.report_critic import _build_critic_messages
    from fii_agents.synthesis_runner import _load_priors_from_cache
    from fii_db import SpecialistCache, Ticker
    from fii_db.session import session_scope
    from fii_shared import ExtractedReportClaims
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    sym = "TSTCRGAP"
    factory = get_runtime().session_factory

    # Wipe + seed: ticker exists, fundamentals fresh, the rest missing.
    with session_scope(factory) as s:
        s.execute(delete(SpecialistCache).where(SpecialistCache.symbol == sym))
        s.execute(
            pg_insert(Ticker)
            .values(symbol=sym, name=sym)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )
        s.execute(
            pg_insert(SpecialistCache).values(
                symbol=sym,
                specialist_name="fundamentals",
                output_json={"qualitative_summary": "stub fundamentals output"},
                citations_json={},
                model_used="fake",
                tokens_in=0,
                tokens_out=0,
                cost_usd=Decimal("0"),
                duration_ms=0,
                status="ok",
                last_run_at=datetime.now(UTC),
                last_input_hash="x",
            )
        )

    priors, missing, stale = _load_priors_from_cache(factory, sym)
    assert "fundamentals" in priors
    # Everything else should be missing.
    assert set(missing) >= {"valuation", "moat", "macro", "technical", "news", "insider", "risk"}
    assert stale == []

    extracted = ExtractedReportClaims(
        symbol=sym,
        report_source="morningstar",
        analyst_name=None,
        publication_date=None,
        recommendation="Buy",
        price_target=None,
        time_horizon=None,
        bull_case_summary="Bull stub.",
        bear_case_summary="Bear stub.",
        key_numerical_claims=[],
        key_qualitative_claims=[],
        stated_assumptions=[],
        analyst_disclosures=[],
    )

    messages = _build_critic_messages(
        critique_id=str(uuid.uuid4()),
        symbol=sym,
        extracted=extracted,
        priors=priors,
        missing=missing,
        stale=stale,
    )
    text = messages[0]["content"][0]["text"]
    assert "<unavailable_specialists>" in text
    # Each missing specialist appears by name in the unavailable block.
    for name in ("valuation", "moat", "macro", "technical", "news", "insider", "risk"):
        assert name in text, f"missing specialist {name} should appear in critic messages"
    # The fundamentals output should have been included in the priors block.
    assert "stub fundamentals output" in text

    with session_scope(factory) as s:
        s.execute(delete(SpecialistCache).where(SpecialistCache.symbol == sym))


# --- 6. Stale-cache marker still flows through ------------------------------------------


def test_stale_specialists_appear_in_unavailable_block():
    from app.agents_runtime import get_runtime
    from fii_agents.specialists.report_critic import _build_critic_messages
    from fii_agents.synthesis_runner import _load_priors_from_cache
    from fii_db import SpecialistCache, Ticker
    from fii_db.session import session_scope
    from fii_shared import ExtractedReportClaims
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    sym = "TSTCRSTALE"
    factory = get_runtime().session_factory
    very_old = datetime.now(UTC) - timedelta(days=400)

    with session_scope(factory) as s:
        s.execute(delete(SpecialistCache).where(SpecialistCache.symbol == sym))
        s.execute(
            pg_insert(Ticker)
            .values(symbol=sym, name=sym)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )
        s.execute(
            pg_insert(SpecialistCache).values(
                symbol=sym,
                specialist_name="news",
                output_json={"net_sentiment": 0.0},
                citations_json={},
                model_used="fake",
                tokens_in=0,
                tokens_out=0,
                cost_usd=Decimal("0"),
                duration_ms=0,
                status="ok",
                last_run_at=very_old,
                last_input_hash="x",
            )
        )

    _, missing, stale = _load_priors_from_cache(factory, sym)
    assert "news" in stale, f"stale list should contain 'news'; got missing={missing} stale={stale}"

    extracted = ExtractedReportClaims(
        symbol=sym,
        report_source="other",
        analyst_name=None,
        publication_date=None,
        recommendation=None,
        price_target=None,
        time_horizon=None,
        bull_case_summary="Bull",
        bear_case_summary="Bear",
        key_numerical_claims=[],
        key_qualitative_claims=[],
        stated_assumptions=[],
        analyst_disclosures=[],
    )
    messages = _build_critic_messages(
        critique_id=str(uuid.uuid4()),
        symbol=sym,
        extracted=extracted,
        priors={},
        missing=missing,
        stale=stale,
    )
    text = messages[0]["content"][0]["text"]
    assert "news" in text
    assert "stale" in text  # the JSON dict literally has "stale": [...]

    with session_scope(factory) as s:
        s.execute(delete(SpecialistCache).where(SpecialistCache.symbol == sym))
