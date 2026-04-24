"""SEC EDGAR client — wraps the `edgartools` library.

edgartools is synchronous and hits SEC directly (no API key; just a user-agent). We bridge
to async via asyncio.to_thread and add our own structured logging for consistency with
the other clients. edgartools already respects SEC's 10 req/sec limit internally.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _set_identity_once() -> None:
    """SEC requires a User-Agent header. edgartools reads it from env EDGAR_IDENTITY."""
    if not os.environ.get("EDGAR_IDENTITY"):
        contact = os.environ.get("SEC_CONTACT_EMAIL", "fii-prism@example.com")
        os.environ["EDGAR_IDENTITY"] = f"FII-PRISM research {contact}"


@dataclass
class FilingRecord:
    accession_no: str
    form_type: str
    filed_at: date
    period_of_report: date | None
    url: str | None
    title: str | None
    raw_text: str | None = None
    sections: dict[str, str] | None = None


@dataclass
class InsiderTrade:
    insider_name: str
    role: str | None
    transaction_type: str | None
    shares: float | None
    price: float | None
    transaction_date: date
    filed_at: date
    accession_no: str | None


class EdgarClient:
    """Thin async facade over edgartools."""

    provider = "edgar"

    def __init__(self) -> None:
        _set_identity_once()
        self._log = log.bind(provider=self.provider)

    async def _run(self, fn, /, *args, **kwargs) -> Any:
        start = time.perf_counter()
        try:
            result = await asyncio.to_thread(fn, *args, **kwargs)
            return result
        finally:
            self._log.info(
                "edgar_call",
                fn=getattr(fn, "__name__", str(fn)),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )

    async def get_latest_10k(self, symbol: str) -> FilingRecord | None:
        """Most recent 10-K with full text + section split."""
        return await self._run(self._fetch_filing_sync, symbol.upper(), "10-K")

    async def get_latest_10q(self, symbol: str) -> FilingRecord | None:
        return await self._run(self._fetch_filing_sync, symbol.upper(), "10-Q")

    async def get_form4s(self, symbol: str, *, since: date) -> list[InsiderTrade]:
        return await self._run(self._fetch_form4s_sync, symbol.upper(), since)

    # --- Sync implementations (executed in a worker thread) -------------------------------

    def _fetch_filing_sync(self, symbol: str, form: str) -> FilingRecord | None:
        from edgar import Company  # lazy import to keep module import cheap

        company = Company(symbol)
        filings = company.get_filings(form=form).latest(1)
        if not filings:
            return None
        filing = filings[0] if hasattr(filings, "__iter__") else filings

        try:
            text = filing.text()  # full cleaned text
        except Exception:
            text = None

        sections: dict[str, str] | None = None
        # edgartools exposes `obj()` for TenK/TenQ which has `.items` section splits.
        try:
            typed = filing.obj()
            items = getattr(typed, "items", None)
            if items:
                sections = {k: str(v) for k, v in items.items() if v}
        except Exception:
            pass

        return FilingRecord(
            accession_no=str(filing.accession_no),
            form_type=str(filing.form),
            filed_at=_to_date(filing.filing_date),
            period_of_report=_to_date(getattr(filing, "period_of_report", None)),
            url=getattr(filing, "filing_url", None) or getattr(filing, "url", None),
            title=str(getattr(filing, "header", None) or symbol),
            raw_text=text,
            sections=sections,
        )

    def _fetch_form4s_sync(self, symbol: str, since: date) -> list[InsiderTrade]:
        from edgar import Company

        company = Company(symbol)
        filings = company.get_filings(form="4")
        out: list[InsiderTrade] = []
        for f in filings:
            filed = _to_date(f.filing_date)
            if filed is None or filed < since:
                continue
            try:
                parsed = f.obj()
            except Exception:
                continue
            transactions = getattr(parsed, "transactions", None) or []
            owner_name = str(getattr(parsed, "owner_name", "")) or "unknown"
            role = str(getattr(parsed, "officer_title", "") or "") or None
            for t in transactions:
                out.append(
                    InsiderTrade(
                        insider_name=owner_name,
                        role=role,
                        transaction_type=str(getattr(t, "code", "") or "") or None,
                        shares=_to_float(getattr(t, "shares", None)),
                        price=_to_float(getattr(t, "price", None)),
                        transaction_date=_to_date(getattr(t, "date", None)) or filed,
                        filed_at=filed,
                        accession_no=str(f.accession_no),
                    )
                )
        return out


def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
