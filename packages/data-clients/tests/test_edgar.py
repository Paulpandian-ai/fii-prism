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


# --- Test fixtures: edgartools 5.30.2 Form 4 / Ownership lookalikes ---------------------


class _FakeTxnsHolder:
    """Mimics edgartools' NonDerivativeTransactions / DerivativeTransactions
    DataHolder: `.empty`, `.data` (sized), and `__getitem__` returning a
    typed-row dataclass. The diagnosed bug was reading parsed.transactions
    directly; the fix reaches through `.non_derivative_table.transactions`
    so the test pins exactly that path."""

    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.data = rows  # len(holder.data) == n_rows; matches the DataFrame surface

    @property
    def empty(self) -> bool:
        return not self._rows

    def __getitem__(self, i: int):
        return self._rows[i]


class _FakeRow:
    """Stands in for the edgartools NonDerivativeTransaction / DerivativeTransaction
    dataclass. Real rows expose .date / .shares / .price / .transaction_code etc."""

    def __init__(self, *, txn_date, shares, price, transaction_code) -> None:
        self.date = txn_date
        self.shares = shares
        self.price = price
        self.transaction_code = transaction_code


class _FakeTable:
    def __init__(self, transactions: _FakeTxnsHolder) -> None:
        self.transactions = transactions


class _FakeOwner:
    def __init__(self, name: str, officer_title: str | None) -> None:
        self.name = name
        self.officer_title = officer_title


class _FakeReportingOwners:
    def __init__(self, owners: list[_FakeOwner]) -> None:
        self.owners = owners


class _FakeForm4Object:
    """Looks like edgartools 5.30.2 Form4(Ownership): top-level
    .non_derivative_table / .derivative_table / .reporting_owners attributes,
    NO top-level .transactions or .owner_name."""

    def __init__(
        self,
        *,
        non_deriv_rows: list[_FakeRow] | None = None,
        deriv_rows: list[_FakeRow] | None = None,
        owners: list[_FakeOwner] | None = None,
    ) -> None:
        self.non_derivative_table = _FakeTable(_FakeTxnsHolder(non_deriv_rows or []))
        self.derivative_table = _FakeTable(_FakeTxnsHolder(deriv_rows or []))
        self.reporting_owners = _FakeReportingOwners(owners or [])


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
        # Use the real edgartools shape so the parse-error test exercises the
        # production extraction path. Owner data lives at reporting_owners.owners[0];
        # transactions live at non_derivative_table.transactions.
        rows = [
            _FakeRow(
                txn_date=t.date,
                shares=t.shares,
                price=t.price,
                transaction_code=t.code,
            )
            for t in self._transactions
        ]
        return _FakeForm4Object(
            non_deriv_rows=rows,
            owners=[_FakeOwner(name=self._owner_name, officer_title="CEO")],
        )


class _FakeTxn:
    """Test-input shape — gets translated to a `_FakeRow` matching edgartools'
    NonDerivativeTransaction dataclass surface inside `_FakeFiling.obj()`."""

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


# --- 4. Form 4 transaction extraction (edgartools 5.30.2 attribute path) -----------------


def test_form4_extraction_reads_non_derivative_table_transactions(monkeypatch, caplog):
    """A Form 4 with non-derivative transactions must yield InsiderTrade rows.
    Pre-fix: the code read parsed.transactions which doesn't exist on the
    Ownership class, returned 0 trades from a successful parse."""
    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    rows = [
        _FakeRow(txn_date="2026-01-05", shares=1000, price=150.0, transaction_code="P"),
        _FakeRow(txn_date="2026-01-06", shares=500, price=152.5, transaction_code="S"),
    ]
    parsed = _FakeForm4Object(
        non_deriv_rows=rows,
        owners=[_FakeOwner(name="Tim Cook", officer_title="CEO")],
    )

    class _OneFiling:
        accession_no = "0000-good"
        filing_date = date(2026, 1, 10)

        def obj(self):
            return parsed

    class _Company:
        def get_filings(self, *, form):
            return [_OneFiling()]

    with patch("edgar.Company", return_value=_Company()):
        client = EdgarClient()
        with caplog.at_level(logging.INFO):
            trades = client._fetch_form4s_sync("AAPL", since=date(2025, 1, 1))

    assert len(trades) == 2, (
        f"expected 2 transactions from non_derivative_table.transactions; got {len(trades)}. "
        "If this regresses to 0, the extractor is again reading parsed.transactions instead "
        "of parsed.non_derivative_table.transactions."
    )
    # Owner data flows through reporting_owners.owners[0], not parsed.owner_name.
    assert trades[0].insider_name == "Tim Cook"
    assert trades[0].role == "CEO"
    # Transaction code is the SEC letter code from .transaction_code.
    assert {t.transaction_type for t in trades} == {"P", "S"}
    assert {t.shares for t in trades} == {1000.0, 500.0}

    summaries = [r for r in caplog.records if r.getMessage() == "edgar_form4_summary"]
    assert summaries
    assert summaries[-1].transactions_emitted == 2


def test_form4_extraction_includes_derivative_transactions(monkeypatch):
    """Both non-derivative and derivative transactions must flow through.
    Option exercises (M codes) live on the derivative table; missing them was
    half the diagnosed gap."""
    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    parsed = _FakeForm4Object(
        non_deriv_rows=[_FakeRow(txn_date="2026-02-01", shares=100, price=10.0, transaction_code="P")],
        deriv_rows=[
            _FakeRow(txn_date="2026-02-02", shares=200, price=0.0, transaction_code="M"),
            _FakeRow(txn_date="2026-02-03", shares=50, price=15.0, transaction_code="A"),
        ],
        owners=[_FakeOwner(name="Luca Maestri", officer_title="CFO")],
    )

    class _OneFiling:
        accession_no = "0000-deriv"
        filing_date = date(2026, 2, 5)

        def obj(self):
            return parsed

    class _Company:
        def get_filings(self, *, form):
            return [_OneFiling()]

    with patch("edgar.Company", return_value=_Company()):
        client = EdgarClient()
        trades = client._fetch_form4s_sync("AAPL", since=date(2025, 1, 1))

    assert len(trades) == 3
    # Each table contributed at least one row.
    codes = sorted(t.transaction_type for t in trades)
    assert codes == ["A", "M", "P"]


def test_form4_zero_transactions_logs_debug(monkeypatch, caplog):
    """When a Form 4 parses but has no transactions on either table (e.g., a
    holdings-only amendment), we must surface that at DEBUG with the parsed_attrs
    sample so a future schema change is greppable."""
    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    parsed = _FakeForm4Object(
        non_deriv_rows=[],
        deriv_rows=[],
        owners=[_FakeOwner(name="Owner", officer_title=None)],
    )

    class _OneFiling:
        accession_no = "0000-empty"
        filing_date = date(2026, 1, 10)

        def obj(self):
            return parsed

    class _Company:
        def get_filings(self, *, form):
            return [_OneFiling()]

    with patch("edgar.Company", return_value=_Company()):
        client = EdgarClient()
        with caplog.at_level(logging.DEBUG):
            trades = client._fetch_form4s_sync("AAPL", since=date(2025, 1, 1))

    assert trades == []
    debug_records = [
        r for r in caplog.records if r.getMessage() == "form4_no_transactions"
    ]
    assert len(debug_records) == 1
    assert debug_records[0].accession_no == "0000-empty"
    # parsed_attrs must include at least one of the actual Ownership-class
    # attributes so a schema migration can be inferred from the log alone.
    assert "non_derivative_table" in debug_records[0].parsed_attrs


# --- 5. seed.py call-site pinning -------------------------------------------------------


def test_seed_calls_10k_and_10q_when_embedder_present(monkeypatch):
    """Pin the call sites: even with mocked-zero ingest functions, the seed
    flow must invoke ingest_latest_10k AND ingest_latest_10q exactly once
    each. Future refactors that drop a step (the 10-K/10-Q skip bug)
    immediately fail this test."""
    import asyncio
    from unittest.mock import AsyncMock

    from fii_ingest.config import Settings
    from fii_ingest.jobs import seed as seed_module

    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    # Replace every external dependency with mocks. We're only verifying call
    # sites here — DB writes are out of scope for this test.
    fake_10k = AsyncMock(return_value={"filing_id": "fake-id-10k", "chunks": 0})
    fake_10q = AsyncMock(return_value={"filing_id": "fake-id-10q", "chunks": 0})
    monkeypatch.setattr(seed_module, "ingest_latest_10k", fake_10k)
    monkeypatch.setattr(seed_module, "ingest_latest_10q", fake_10q)
    monkeypatch.setattr(seed_module, "ingest_fundamentals", AsyncMock(return_value=0))
    monkeypatch.setattr(seed_module, "ingest_insiders", AsyncMock(return_value=0))
    monkeypatch.setattr(seed_module, "ingest_company_news", AsyncMock(return_value=0))
    monkeypatch.setattr(seed_module, "ingest_daily", AsyncMock(return_value=0))
    monkeypatch.setattr(seed_module, "ingest_intraday", AsyncMock(return_value=0))

    class _NoopEmbedder:
        model = "shim"

        async def embed(self, texts, *, input_type="document"):
            return type("R", (), {"vectors": [[0.0]] * len(texts), "model": "shim"})()

    monkeypatch.setattr(seed_module, "make_embedder", lambda: _NoopEmbedder())

    # EdgarClient and the FMP/Polygon/Finnhub/Fred clients are gated by env var
    # presence. We bypass them by stubbing the constructors to async-noop.
    class _NoopAsyncCtx:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        # Methods called inside the seed orchestration:
        async def get_profile(self, *_):
            return {}

        async def get_series(self, *_):
            return []

    monkeypatch.setattr(seed_module, "FMPClient", _NoopAsyncCtx)
    monkeypatch.setattr(seed_module, "PolygonClient", _NoopAsyncCtx)
    monkeypatch.setattr(seed_module, "FinnhubClient", _NoopAsyncCtx)
    monkeypatch.setattr(seed_module, "FredClient", _NoopAsyncCtx)
    monkeypatch.setattr(seed_module, "EdgarClient", lambda: object())

    monkeypatch.setattr(
        seed_module,
        "upsert_from_fmp_profile",
        lambda *_a, **_k: None,
    )
    # session_scope is a context manager; we monkeypatch its factory entry too.
    monkeypatch.setattr(seed_module, "get_engine", lambda _: None)
    fake_factory = object()
    monkeypatch.setattr(seed_module, "get_session_factory", lambda _: fake_factory)

    class _FakeSession:
        def execute(self, *_, **__):
            return type("R", (), {"scalar_one_or_none": lambda self: None})()

        def add(self, *_):
            pass

    class _SessionScope:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return _FakeSession()

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(seed_module, "session_scope", _SessionScope)

    # Force include_filings True with FMP set so the FMP block runs (it'll noop).
    settings = Settings(
        database_url="postgresql+psycopg://x:y@localhost/db",
        fmp_api_key="x",
        polygon_api_key=None,
        finnhub_api_key=None,
        fred_api_key=None,
        raw_data_bucket=None,
    )

    async def _run():
        # ingest_all_default also lives on this module path; replace it.
        async def _macro(_factory, *, fred):
            from fii_ingest.jobs.macro import MacroIngestReport

            return MacroIngestReport(total_rows=0, failed=[])

        monkeypatch.setattr(seed_module, "ingest_all_default", _macro)
        return await seed_module.seed_ticker(
            settings,
            symbol="AAPL",
            years_back=1,
            include_macro=False,
            include_filings=True,
            include_embeddings=True,
        )

    asyncio.run(_run())
    fake_10k.assert_awaited_once()
    fake_10q.assert_awaited_once()


def test_seed_logs_skipped_when_embedder_unavailable(monkeypatch, caplog):
    """The Bug 2 regression test: when the embedder is None, the 10-K/10-Q
    block must emit a seed_step_skipped log line (was: silent skip, no log).
    """
    import asyncio
    from unittest.mock import AsyncMock

    from fii_ingest.config import Settings
    from fii_ingest.jobs import seed as seed_module

    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    fake_10k = AsyncMock(return_value={"filing_id": None, "chunks": 0})
    fake_10q = AsyncMock(return_value={"filing_id": None, "chunks": 0})
    monkeypatch.setattr(seed_module, "ingest_latest_10k", fake_10k)
    monkeypatch.setattr(seed_module, "ingest_latest_10q", fake_10q)
    monkeypatch.setattr(seed_module, "ingest_fundamentals", AsyncMock(return_value=0))
    monkeypatch.setattr(seed_module, "ingest_insiders", AsyncMock(return_value=0))
    monkeypatch.setattr(seed_module, "ingest_company_news", AsyncMock(return_value=0))
    monkeypatch.setattr(seed_module, "ingest_daily", AsyncMock(return_value=0))
    monkeypatch.setattr(seed_module, "ingest_intraday", AsyncMock(return_value=0))

    # make_embedder raises -> embedder ends up None.
    def _fail():
        raise RuntimeError("no embedder available in test")

    monkeypatch.setattr(seed_module, "make_embedder", _fail)

    class _NoopAsyncCtx:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get_profile(self, *_):
            return {}

    monkeypatch.setattr(seed_module, "FMPClient", _NoopAsyncCtx)
    monkeypatch.setattr(seed_module, "PolygonClient", _NoopAsyncCtx)
    monkeypatch.setattr(seed_module, "FinnhubClient", _NoopAsyncCtx)
    monkeypatch.setattr(seed_module, "FredClient", _NoopAsyncCtx)
    monkeypatch.setattr(seed_module, "EdgarClient", lambda: object())
    monkeypatch.setattr(
        seed_module,
        "upsert_from_fmp_profile",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(seed_module, "get_engine", lambda _: None)
    monkeypatch.setattr(seed_module, "get_session_factory", lambda _: object())

    class _FakeSession:
        def execute(self, *_, **__):
            return type("R", (), {"scalar_one_or_none": lambda self: None})()

        def add(self, *_):
            pass

    class _SessionScope:
        def __init__(self, *_):
            pass

        def __enter__(self):
            return _FakeSession()

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(seed_module, "session_scope", _SessionScope)

    settings = Settings(
        database_url="postgresql+psycopg://x:y@localhost/db",
        fmp_api_key="x",
        polygon_api_key=None,
        finnhub_api_key=None,
        fred_api_key=None,
        raw_data_bucket=None,
    )

    async def _run():
        return await seed_module.seed_ticker(
            settings,
            symbol="AAPL",
            years_back=1,
            include_macro=False,
            include_filings=True,
            include_embeddings=True,
        )

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run())

    skipped = [r for r in caplog.records if r.getMessage() == "seed_step_skipped"]
    # Both 10-K and 10-Q must surface as skipped.
    steps = sorted({r.step for r in skipped})
    assert "10k" in steps and "10q" in steps, f"got skipped steps {steps}"
    # And the ingest functions must NOT have been called when embedder is None.
    fake_10k.assert_not_awaited()
    fake_10q.assert_not_awaited()
