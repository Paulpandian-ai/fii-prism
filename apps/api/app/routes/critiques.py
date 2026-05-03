"""Analyst-report critique API.

- POST /critiques/upload                       → multipart upload, dedupe by hash
- POST /critiques/{critique_id}/run            → idempotent two-call workflow
- GET  /critiques/{critique_id}                → fetch a persisted critique
- GET  /stocks/{symbol}/critiques              → list critiques for a ticker
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fii_agents.specialists.report_critic import run_report_critique
from fii_db import ReportCritique, Ticker
from fii_db.session import session_scope
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy import update as sql_update

from app.agents_runtime import AgentsRuntime, get_runtime
from app.cost_gate import DailyCapExceeded, assert_within_daily_cap

log = structlog.get_logger(__name__)

# ruff: noqa: B008
router = APIRouter(prefix="/critiques", tags=["critiques"])
stocks_router = APIRouter(prefix="/stocks", tags=["critiques"])


# 25MB cap on uploaded PDFs. Stored inline as BYTEA — the model's input cap is
# enforced server-side by Anthropic; this limit just keeps the table healthy.
MAX_PDF_BYTES = 25 * 1024 * 1024


# --- Response models ---------------------------------------------------------------------


class CritiqueRow(BaseModel):
    """Catalog summary used by GET /stocks/{symbol}/critiques + GET /critiques/{id}."""

    critique_id: str
    symbol: str
    report_filename: str
    report_source: str | None
    pdf_hash: str
    status: str
    cost_usd: float
    tokens_in: int
    tokens_out: int
    duration_ms: int
    model_used: str | None
    created_at: datetime
    completed_at: datetime | None
    extracted_claims: dict[str, Any] | None = None
    critique: dict[str, Any] | None = None
    # One-line verdict + reliability are surfaced top-level for list views even
    # when the full critique JSON is suppressed.
    one_line_verdict: str | None = None
    reliability_rating: str | None = None


class UploadResponse(BaseModel):
    critique_id: str
    status: str
    deduped: bool


# --- Helpers -----------------------------------------------------------------------------


def _row_to_summary(row: ReportCritique, *, include_full: bool) -> CritiqueRow:
    crit_json = row.critique_json if include_full else None
    extracted_json = row.extracted_claims_json if include_full else None
    one_line = None
    rating = None
    if isinstance(row.critique_json, dict):
        v = row.critique_json.get("one_line_verdict")
        one_line = str(v) if v else None
        r = row.critique_json.get("reliability_rating")
        rating = str(r) if r else None
    return CritiqueRow(
        critique_id=str(row.critique_id),
        symbol=row.symbol,
        report_filename=row.report_filename,
        report_source=row.report_source,
        pdf_hash=row.pdf_hash,
        status=row.status,
        cost_usd=float(row.cost_usd or 0),
        tokens_in=int(row.tokens_in or 0),
        tokens_out=int(row.tokens_out or 0),
        duration_ms=int(row.duration_ms or 0),
        model_used=row.model_used,
        created_at=row.created_at,
        completed_at=row.completed_at,
        extracted_claims=extracted_json,
        critique=crit_json,
        one_line_verdict=one_line,
        reliability_rating=rating,
    )


# --- Routes ------------------------------------------------------------------------------


@router.post("/upload", response_model=UploadResponse, status_code=201)
async def upload_critique(
    symbol: str = Form(...),
    report_source: str = Form(...),
    pdf: UploadFile = File(...),
    runtime: AgentsRuntime = Depends(get_runtime),
) -> UploadResponse:
    """Accept a PDF + symbol/source. Validates ticker, dedupes by sha256, and
    creates a row with status='pending'. Returns the (existing or new)
    critique_id so the client navigates to the same detail URL either way."""
    if pdf.content_type not in ("application/pdf", "application/x-pdf", None):
        # Some browsers send None for content_type; trust the filename suffix below.
        raise HTTPException(status_code=415, detail="upload must be a PDF")
    fname = (pdf.filename or "report.pdf").strip() or "report.pdf"
    if not fname.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="filename must end in .pdf")

    body = await pdf.read()
    if not body:
        raise HTTPException(status_code=400, detail="empty file")
    if len(body) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF too large ({len(body)} bytes; max {MAX_PDF_BYTES})",
        )

    sym = symbol.strip().upper()
    pdf_hash = hashlib.sha256(body).hexdigest()

    with session_scope(runtime.session_factory) as s:
        ticker = s.execute(select(Ticker).where(Ticker.symbol == sym)).scalar_one_or_none()
        if ticker is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "ticker_not_found", "symbol": sym},
            )

        existing = s.execute(
            select(ReportCritique).where(ReportCritique.pdf_hash == pdf_hash)
        ).scalar_one_or_none()
        if existing is not None:
            log.info(
                "critique_upload_deduped",
                critique_id=str(existing.critique_id),
                pdf_hash=pdf_hash,
            )
            return UploadResponse(
                critique_id=str(existing.critique_id),
                status=existing.status,
                deduped=True,
            )

        critique_id = str(uuid.uuid4())
        s.add(
            ReportCritique(
                critique_id=critique_id,
                symbol=sym,
                report_filename=fname[:255],
                report_source=report_source[:64] if report_source else None,
                pdf_hash=pdf_hash,
                pdf_bytes=body,
                status="pending",
            )
        )
    log.info("critique_upload_created", critique_id=critique_id, symbol=sym, bytes=len(body))
    return UploadResponse(critique_id=critique_id, status="pending", deduped=False)


@router.post("/{critique_id}/run", response_model=CritiqueRow)
async def run_critique(
    critique_id: str, runtime: AgentsRuntime = Depends(get_runtime)
) -> CritiqueRow:
    """Run the two-call critic workflow. Idempotent — if status='ok' already,
    we return the cached row without re-invoking Claude."""
    with session_scope(runtime.session_factory) as s:
        row = s.execute(
            select(ReportCritique).where(ReportCritique.critique_id == critique_id)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="critique not found")
        if row.status == "ok":
            return _row_to_summary(row, include_full=True)
        sym = row.symbol
        report_source = row.report_source or "other"
        pdf_bytes = row.pdf_bytes

        # Mark as running so a concurrent re-run doesn't double-spend.
        s.execute(
            sql_update(ReportCritique)
            .where(ReportCritique.critique_id == critique_id)
            .values(status="running")
        )

    try:
        assert_within_daily_cap(runtime.session_factory)
    except DailyCapExceeded as exc:
        # Roll the status back to 'pending' so the user can retry once the cap resets.
        with session_scope(runtime.session_factory) as s:
            s.execute(
                sql_update(ReportCritique)
                .where(ReportCritique.critique_id == critique_id)
                .values(status="pending")
            )
        raise HTTPException(status_code=429, detail=str(exc)) from None

    result = await run_report_critique(
        factory=runtime.session_factory,
        symbol=sym,
        critique_id=critique_id,
        report_source=report_source,
        pdf_bytes=pdf_bytes,
    )

    persist_status = result.status if result.status != "ok" else "ok"
    completed_at = datetime.now(UTC) if result.status == "ok" else None
    with session_scope(runtime.session_factory) as s:
        s.execute(
            sql_update(ReportCritique)
            .where(ReportCritique.critique_id == critique_id)
            .values(
                extracted_claims_json=result.extracted_claims,
                critique_json=result.critique,
                status=persist_status,
                cost_usd=Decimal(str(round(result.cost_usd, 4))),
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                duration_ms=result.duration_ms,
                model_used=result.model_used,
                completed_at=completed_at,
            )
        )
        refreshed = s.execute(
            select(ReportCritique).where(ReportCritique.critique_id == critique_id)
        ).scalar_one()
        return _row_to_summary(refreshed, include_full=True)


@router.get("/{critique_id}", response_model=CritiqueRow)
async def get_critique(
    critique_id: str, runtime: AgentsRuntime = Depends(get_runtime)
) -> CritiqueRow:
    with session_scope(runtime.session_factory) as s:
        row = s.execute(
            select(ReportCritique).where(ReportCritique.critique_id == critique_id)
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="critique not found")
        return _row_to_summary(row, include_full=True)


@stocks_router.get("/{symbol}/critiques", response_model=list[CritiqueRow])
async def list_stock_critiques(
    symbol: str, runtime: AgentsRuntime = Depends(get_runtime)
) -> list[CritiqueRow]:
    sym = symbol.upper()
    with session_scope(runtime.session_factory) as s:
        rows = (
            s.execute(
                select(ReportCritique)
                .where(ReportCritique.symbol == sym)
                .order_by(desc(ReportCritique.created_at))
                .limit(20)
            )
            .scalars()
            .all()
        )
        # List view omits the heavy critique_json + extracted_claims_json payload —
        # the detail page fetches the full row.
        return [_row_to_summary(r, include_full=False) for r in rows]
