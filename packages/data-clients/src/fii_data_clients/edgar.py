"""SEC EDGAR client — wraps the `edgartools` library.

edgartools is synchronous and hits SEC directly (no API key; just a user-agent). We bridge
to async via asyncio.to_thread and add our own structured logging for consistency with
the other clients. edgartools already respects SEC's 10 req/sec limit internally.

SEC compliance: every request must carry a User-Agent identifying a real, deliverable
contact email. Generic example.com addresses are rejected with HTTP 403. We refuse to
construct an EdgarClient when SEC_CONTACT_EMAIL is missing or set to an example.com
address — failing fast at startup is better than silent zero-row seed reports.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# Tightly anchored to keep "anything-with-an-at-sign" out — SEC rejects fake emails too.
_ISO_DATE_REGEX = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _set_identity_once() -> None:
    """SEC requires a User-Agent header. edgartools reads it from env EDGAR_IDENTITY.

    Two paths:
      1. Caller sets EDGAR_IDENTITY directly — we trust them and don't second-guess.
      2. Caller sets SEC_CONTACT_EMAIL — we build the identity string from it.

    If neither is set, OR if SEC_CONTACT_EMAIL points at example.com, we raise. The
    previous default of `fii-prism@example.com` produced HTTP 403 from SEC and quiet
    zero-row seeds; failing loudly here is the right trade-off.
    """
    if os.environ.get("EDGAR_IDENTITY"):
        return
    contact = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    if not contact or contact.lower().endswith("@example.com"):
        raise RuntimeError(
            "SEC_CONTACT_EMAIL must be set to a real, deliverable email address. "
            "SEC EDGAR requires this for compliance. example.com addresses are "
            "rejected with HTTP 403. Set SEC_CONTACT_EMAIL=you@yourdomain.com or "
            "set EDGAR_IDENTITY directly to override."
        )
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
            log.info("edgar_no_filings_found", symbol=symbol, form=form)
            return None
        filing = filings[0] if hasattr(filings, "__iter__") else filings

        try:
            text = filing.text()  # full cleaned text
        except Exception as exc:
            log.warning(
                "edgar_parse_failed",
                fn="fetch_filing_text",
                symbol=symbol,
                form=form,
                exc=str(exc),
                exc_type=type(exc).__name__,
            )
            text = None

        sections: dict[str, str] | None = None
        # edgartools 5.30.2 changed `TenK.items` / `TenQ.items` from a
        # dict[str, str] to a list[str] of "Item X" names; the body text now
        # lives behind __getitem__ on the typed wrapper. We tolerate either
        # shape so a future revert (or a third shape) doesn't reintroduce
        # the silent-zero-sections regression.
        try:
            typed = filing.obj()
            items = getattr(typed, "items", None)
            if isinstance(items, list) and items:
                sections = {}
                skipped: list[tuple[str, str]] = []
                for name in items:
                    try:
                        body = typed[name]
                    except Exception as item_exc:
                        skipped.append((str(name), type(item_exc).__name__))
                        continue
                    if body:
                        sections[str(name)] = str(body)
                if skipped:
                    # One log line summarises the whole batch so a future
                    # edgartools schema shift surfaces with the affected names.
                    log.warning(
                        "edgar_section_items_skipped",
                        symbol=symbol,
                        form=form,
                        count=len(skipped),
                        details=skipped,
                    )
                if not sections:
                    sections = None  # downstream falls back to whole-filing chunking
            elif isinstance(items, dict) and items:
                # Backward-compat for older edgartools that returned a dict.
                sections = {str(k): str(v) for k, v in items.items() if v}
        except Exception as exc:
            log.warning(
                "edgar_parse_failed",
                fn="extract_filing_sections",
                symbol=symbol,
                form=form,
                exc=str(exc),
                exc_type=type(exc).__name__,
            )
            sections = None

        record = FilingRecord(
            accession_no=str(filing.accession_no),
            form_type=str(filing.form),
            filed_at=_to_date(filing.filing_date),
            period_of_report=_to_date(getattr(filing, "period_of_report", None)),
            url=getattr(filing, "filing_url", None) or getattr(filing, "url", None),
            title=str(getattr(filing, "header", None) or symbol),
            raw_text=text,
            sections=sections,
        )
        log.info(
            "edgar_filing_summary",
            symbol=symbol,
            form=form,
            accession_no=record.accession_no,
            filed_at=record.filed_at.isoformat() if record.filed_at else None,
            text_len=(len(text) if text else 0),
            sections_count=(len(sections) if sections else 0),
        )
        return record

    def _fetch_form4s_sync(self, symbol: str, since: date) -> list[InsiderTrade]:
        from edgar import Company

        company = Company(symbol)
        filings = company.get_filings(form="4")
        out: list[InsiderTrade] = []
        n_raw = 0
        n_filtered = 0
        n_parsed = 0
        for f in filings:
            n_raw += 1
            filed = _to_date(f.filing_date)
            if filed is None or filed < since:
                continue
            n_filtered += 1
            try:
                parsed = f.obj()
            except Exception as exc:
                log.warning(
                    "edgar_parse_failed",
                    fn="parse_form4_obj",
                    symbol=symbol,
                    accession_no=str(getattr(f, "accession_no", "")),
                    exc=str(exc),
                    exc_type=type(exc).__name__,
                )
                continue
            n_parsed += 1
            owner_name, role = _form4_owner(parsed)
            txns = _form4_transactions(parsed, accession_no=str(f.accession_no))
            if not txns:
                # Surface zero-transaction parses at DEBUG so a future edgartools
                # schema change shows up loudly in seed logs (with sampled `dir`).
                log.debug(
                    "form4_no_transactions",
                    symbol=symbol,
                    accession_no=str(getattr(f, "accession_no", "")),
                    parsed_attrs=str([a for a in dir(parsed) if not a.startswith("_")])[:200],
                )
            for t in txns:
                out.append(
                    InsiderTrade(
                        insider_name=owner_name,
                        role=role,
                        transaction_type=t["transaction_type"],
                        shares=t["shares"],
                        price=t["price"],
                        transaction_date=t["transaction_date"] or filed,
                        filed_at=filed,
                        accession_no=str(f.accession_no),
                    )
                )
        # Single-line counter so the seed log makes the failure mode obvious:
        # auth (raw=0), filter (filtered=0), parse (parsed=0), or success (parsed>0).
        log.info(
            "edgar_form4_summary",
            symbol=symbol,
            since=since.isoformat(),
            raw_filings_returned=n_raw,
            after_date_filter=n_filtered,
            after_parse=n_parsed,
            transactions_emitted=len(out),
        )
        return out


def _form4_owner(parsed: Any) -> tuple[str, str | None]:
    """Pull (name, role) from a parsed edgartools Form 4 / Ownership object.

    edgartools 5.30.2 exposes owner data via parsed.reporting_owners.owners — a
    list of Owner dataclasses with `name`, `officer_title`, `is_officer`,
    `is_director` etc. The previous code read parsed.owner_name / .officer_title
    directly which doesn't exist on the Form4 class and silently returned
    "unknown" / None.

    Returns ("unknown", None) when the structure is missing or empty rather
    than raising — single-Form4 parse failures shouldn't kill the whole loop.
    """
    try:
        owners = getattr(getattr(parsed, "reporting_owners", None), "owners", None) or []
        if not owners:
            return "unknown", None
        first = owners[0]
        name = (getattr(first, "name", None) or "").strip() or "unknown"
        title_attr = getattr(first, "officer_title", None) or ""
        role: str | None = title_attr.strip() if isinstance(title_attr, str) else None
        if not role:
            # Fall back to the position derivation used by edgartools' display logic.
            position = getattr(first, "position", None)
            role = position if isinstance(position, str) and position else None
        return name, role
    except Exception as exc:
        log.debug("form4_owner_extract_failed", exc=str(exc))
        return "unknown", None


def _form4_transactions(parsed: Any, *, accession_no: str) -> list[dict[str, Any]]:
    """Pull every transaction (non-derivative AND derivative) out of a parsed
    edgartools Form 4 / Ownership object.

    edgartools 5.30.2 schema:
      - parsed.non_derivative_table.transactions: NonDerivativeTransactions(DataHolder)
      - parsed.derivative_table.transactions:     DerivativeTransactions(DataHolder)

    Both wrap a pandas DataFrame at `.data` and support __getitem__ → returns a
    typed dataclass with `date`, `shares`, `price`, `transaction_code`,
    `acquired_disposed`, `direct_indirect`, `security`, `transaction_type`.

    We unify into a flat list of dicts (the InsiderTrade builder above turns
    them into rows). transaction_type is the SEC code (P, S, A, M, F...) so it
    matches the existing schema's expectation; the human-readable label lives
    on the dataclass too if we ever want to surface it.
    """
    out: list[dict[str, Any]] = []
    for table_name in ("non_derivative_table", "derivative_table"):
        table = getattr(parsed, table_name, None)
        if table is None:
            continue
        txns_holder = getattr(table, "transactions", None)
        if txns_holder is None:
            continue
        # DataHolder.empty / .data are the documented surfaces.
        if getattr(txns_holder, "empty", True):
            continue
        data = getattr(txns_holder, "data", None)
        if data is None:
            continue
        try:
            n = len(data)
        except Exception:
            n = 0
        for i in range(n):
            try:
                t = txns_holder[i]
            except Exception as exc:
                log.warning(
                    "edgar_parse_failed",
                    fn="parse_form4_transaction_row",
                    accession_no=accession_no,
                    table=table_name,
                    index=i,
                    exc=str(exc),
                    exc_type=type(exc).__name__,
                )
                continue
            out.append(
                {
                    "transaction_date": _to_date(getattr(t, "date", None)),
                    "shares": _to_float(getattr(t, "shares", None)),
                    "price": _to_float(getattr(t, "price", None)),
                    # SEC transaction code: P (purchase), S (sale), A (grant),
                    # M (option exercise), F (tax withholding), G (gift), etc.
                    "transaction_type": (
                        str(getattr(t, "transaction_code", "") or "") or None
                    ),
                }
            )
    return out


def _to_date(v: Any) -> date | None:
    """Permissive date parser. Order: existing date instance, ISO YYYY-MM-DD prefix,
    full-datetime ISO string, regex extraction of YYYY-MM-DD anywhere in the string,
    None.

    Logs at DEBUG when fallbacks fire so we can see whether non-standard date types
    (e.g., pyarrow.lib.Date returning ``Date(2024-12-30)`` from __str__) are
    contributing to the diagnostic gap.
    """
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v)
    # 1. ISO prefix.
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        pass
    # 2. Full-datetime ISO string (e.g., '2024-12-30T00:00:00+00:00').
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        pass
    # 3. Regex extraction — handles wrappers like 'Date(2024-12-30)' that some
    #    edgartools versions emit via __repr__/__str__ on pyarrow date objects.
    m = _ISO_DATE_REGEX.search(s)
    if m:
        try:
            d = date.fromisoformat(m.group(1))
            log.debug("edgar_date_fallback_regex", input=s, parsed=d.isoformat())
            return d
        except Exception:
            pass
    log.debug("edgar_date_unparseable", input=s, type=type(v).__name__)
    return None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
