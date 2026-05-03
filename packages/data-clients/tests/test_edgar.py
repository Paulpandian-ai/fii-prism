"""Unit tests for the EDGAR client.

Three failure modes the seed pipeline kept hitting silently:
  1. Form 4 .obj() raises and the loop drops every filing without logging.
  2. SEC_CONTACT_EMAIL defaults to example.com and SEC returns 403.
  3. _to_date returns None on non-ISO inputs (pyarrow Date wrappers).

The fixes log at WARNING with structured event names; we verify the exact
log shapes here so the seed log becomes self-diagnosing.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
import structlog
from fii_data_clients.edgar import (
    EdgarClient,
    InsiderTrade,
    _set_identity_once,
    _to_date,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with both env vars unset so we control identity setup."""
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)


@pytest.fixture(autouse=True)
def _structlog_to_stdlib():
    """Route structlog through stdlib logging so pytest's caplog captures the events."""
    structlog.configure(
        processors=[structlog.stdlib.render_to_log_kwargs],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


# --- 1. Form 4 parse-error logging + loop continuation ----------------------------------


class _FakeFiling:
    """edgartools `Filing` lookalike. .obj() raises by default; subclasses flip flag."""

    def __init__(
        self,
        accession_no: str,
        filing_date: date,
        *,
        raise_on_obj: bool = False,
        owner_name: str = "Tim Cook",
        transactions: list | None = None,
    ) -> None:
        self.accession_no = accession_no
        self.filing_date = filing_date
        self._raise = raise_on_obj
        self._owner_name = owner_name
        self._transactions = transactions or []

    def obj(self):
        if self._raise:
            raise ValueError("simulated edgartools parse failure")
        return _FakeForm4Doc(self._owner_name, self._transactions)


class _FakeForm4Doc:
    def __init__(self, owner_name: str, transactions: list) -> None:
        self.owner_name = owner_name
        self.officer_title = "CEO"
        self.transactions = transactions


class _FakeTxn:
    def __init__(self, code: str, shares: float, price: float, txn_date: date) -> None:
        self.code = code
        self.shares = shares
        self.price = price
        self.date = txn_date


class _FakeCompany:
    def __init__(self, filings: list[_FakeFiling]) -> None:
        self._filings = filings

    def get_filings(self, *, form: str):
        return self._filings


def test_form4_parse_error_logs_and_continues(monkeypatch, caplog):
    """When f.obj() raises, the loop must (a) log edgar_parse_failed with
    fn=parse_form4_obj, (b) continue to the next filing, (c) return whatever
    parsed successfully."""
    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    good_txn = _FakeTxn(code="P", shares=1000, price=150.0, txn_date=date(2026, 1, 5))
    filings = [
        _FakeFiling("0000-bad-1", date(2026, 1, 1), raise_on_obj=True),
        _FakeFiling(
            "0000-good-1",
            date(2026, 1, 10),
            owner_name="Tim Cook",
            transactions=[good_txn],
        ),
        _FakeFiling("0000-bad-2", date(2026, 1, 15), raise_on_obj=True),
    ]

    company = _FakeCompany(filings)
    with patch("edgar.Company", return_value=company):
        client = EdgarClient()
        with caplog.at_level(logging.WARNING):
            trades = client._fetch_form4s_sync("AAPL", since=date(2025, 1, 1))

    # (c) function returned the one parseable trade — did NOT re-raise.
    assert len(trades) == 1
    assert isinstance(trades[0], InsiderTrade)
    assert trades[0].insider_name == "Tim Cook"
    assert trades[0].accession_no == "0000-good-1"

    # (a) two parse failures logged, both with fn=parse_form4_obj. structlog's
    # render_to_log_kwargs puts the event_name in the message and the kwargs
    # as attributes on the LogRecord.
    parse_records = [
        r for r in caplog.records if r.getMessage() == "edgar_parse_failed"
    ]
    assert len(parse_records) == 2
    for r in parse_records:
        assert r.fn == "parse_form4_obj"
        assert r.symbol == "AAPL"
        assert r.exc_type == "ValueError"
        assert "simulated edgartools parse failure" in r.exc

    # The form4 summary log MUST also fire — that's what tells the seed log
    # whether the gap is auth (raw=0), filter (filtered=0), or parse (parsed=0).
    # caplog defaults to WARNING; we explicitly want INFO too for this assertion.
    summaries = [
        r for r in caplog.records if r.getMessage() == "edgar_form4_summary"
    ]
    # The summary is emitted at INFO; caplog.at_level(WARNING) above suppresses
    # it, so we re-run with INFO captured for this assertion.
    if not summaries:
        with caplog.at_level(logging.INFO), patch("edgar.Company", return_value=company):
            client._fetch_form4s_sync("AAPL", since=date(2025, 1, 1))
        summaries = [
            r for r in caplog.records if r.getMessage() == "edgar_form4_summary"
        ]
    assert len(summaries) >= 1
    s = summaries[-1]
    assert s.symbol == "AAPL"
    assert s.raw_filings_returned == 3
    assert s.after_date_filter == 3  # all three pass the 2025-01-01 cutoff
    assert s.after_parse == 1  # only the good one parsed
    assert s.transactions_emitted == 1


# --- 2. Identity check rejects example.com ----------------------------------------------


def test_set_identity_raises_when_no_email_set():
    with pytest.raises(RuntimeError, match="SEC_CONTACT_EMAIL"):
        _set_identity_once()


def test_set_identity_raises_on_example_com(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "fii-prism@example.com")
    with pytest.raises(RuntimeError, match=r"example\.com"):
        _set_identity_once()


def test_set_identity_accepts_real_email(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "research@yourdomain.com")
    _set_identity_once()
    assert os.environ.get("EDGAR_IDENTITY") == "FII-PRISM research research@yourdomain.com"


def test_set_identity_respects_explicit_override(monkeypatch):
    """Direct EDGAR_IDENTITY override skips the email check entirely so callers
    who already have a compliant identity string don't have to re-derive it."""
    monkeypatch.setenv("EDGAR_IDENTITY", "Existing custom identity me@elsewhere.com")
    _set_identity_once()
    # Unchanged.
    assert os.environ["EDGAR_IDENTITY"] == "Existing custom identity me@elsewhere.com"


# --- 3. _to_date permissive parsing ------------------------------------------------------


class _PyArrowDateLookalike:
    """Mimics how some pyarrow date wrappers stringify: 'Date(YYYY-MM-DD)'."""

    def __init__(self, iso: str) -> None:
        self._iso = iso

    def __str__(self) -> str:
        return f"Date({self._iso})"


def test_to_date_accepts_real_date():
    d = date(2026, 1, 15)
    assert _to_date(d) == d


def test_to_date_accepts_iso_string():
    assert _to_date("2024-12-30") == date(2024, 12, 30)


def test_to_date_accepts_long_iso_with_time():
    assert _to_date("2024-12-30T13:45:00") == date(2024, 12, 30)


def test_to_date_accepts_iso_with_timezone():
    # Slicing the [:10] prefix handles this in the existing fast path.
    assert _to_date("2024-12-30T13:45:00+00:00") == date(2024, 12, 30)


def test_to_date_unwraps_pyarrow_date_string_via_regex():
    """The crucial case: `Date(2024-12-30)` becomes 2024-12-30 instead of None."""
    assert _to_date(_PyArrowDateLookalike("2024-12-30")) == date(2024, 12, 30)


def test_to_date_handles_datetime_instance():
    dt = datetime(2024, 5, 1, 12, 30, tzinfo=UTC)
    assert _to_date(dt) == date(2024, 5, 1)


def test_to_date_returns_none_on_garbage_without_raising():
    assert _to_date("not a date") is None
    assert _to_date(object()) is None  # str(object()) → '<object ...>'
    assert _to_date(12345) is None
    assert _to_date(None) is None
