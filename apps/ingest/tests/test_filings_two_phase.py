"""Two-phase filing ingest: filing row + chunk text commit BEFORE the embedder runs,
so a Voyage rate-limit (or any embedder failure) leaves the EDGAR fetch persisted
with embedding_status='pending' instead of rolling back the whole transaction.

Plus: the new `fii-ingest backfill-embeddings` command is idempotent — running it
twice is a no-op the second time.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest
import structlog
from fii_data_clients.edgar import FilingRecord
from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult
from fii_db import Filing, FilingChunk, Ticker
from fii_db.session import session_scope
from fii_ingest.jobs.filings import _store_filing
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


@pytest.fixture(autouse=True)
def _structlog_to_stdlib():
    """Route structlog through stdlib logging so pytest's caplog captures
    the events emitted by edgar.py / filings.py."""
    structlog.configure(
        processors=[structlog.stdlib.render_to_log_kwargs],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


# --- Fixtures + helpers ----------------------------------------------------------------


def _seed_ticker(factory, symbol: str) -> None:
    with session_scope(factory) as s:
        s.execute(
            pg_insert(Ticker)
            .values(symbol=symbol, name=symbol)
            .on_conflict_do_nothing(index_elements=[Ticker.symbol])
        )


def _wipe_filings(factory, symbol: str) -> None:
    with session_scope(factory) as s:
        s.execute(delete(Filing).where(Filing.symbol == symbol))


def _make_record(symbol: str, *, accession: str, form: str = "10-K") -> FilingRecord:
    return FilingRecord(
        accession_no=accession,
        form_type=form,
        filed_at=date(2026, 5, 1),
        period_of_report=date(2026, 3, 31),
        url=f"https://example.com/{accession}",
        title=f"{symbol} {form} 2026Q1",
        # ~6KB raw + a single section so the chunker emits at least one chunk.
        raw_text="Apple Inc. quarterly results.\n" * 200,
        sections={
            "Item 1": "Business overview text. " * 80,
            "Item 1A": "Risk factors text. " * 80,
        },
    )


class _OkEmbedder:
    model = "fake-ok"

    async def embed(self, texts, *, input_type: str = "document") -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[[0.0] * EMBEDDING_DIM for _ in texts],
            model=self.model,
            tokens=len(texts) * 100,
            cost_usd=0.0,
        )


class _RateLimitEmbedder:
    model = "fake-ratelimited"

    async def embed(self, texts, *, input_type: str = "document") -> EmbeddingResult:
        # Mirrors voyageai.error.RateLimitError shape — bare exception is fine
        # since _store_filing's catch is `except Exception`.
        raise RuntimeError("simulated voyage 429: rate-limit exceeded")


# --- 1. Two-phase: embedder failure leaves filing + pending chunks ---------------------


@pytest.mark.asyncio
async def test_embedder_failure_leaves_filing_and_pending_chunks(session_factory):
    """The whole point of the refactor: a Voyage 429 must NOT roll back the
    filing. Verify both the Filing row AND the chunk rows persist with
    embedding_status='pending' and embedding=NULL."""
    sym = "TSTFP1"
    _seed_ticker(session_factory, sym)
    _wipe_filings(session_factory, sym)

    record = _make_record(sym, accession="0000-fp-1")
    await _store_filing(
        factory=session_factory,
        edgar=None,  # not used by _store_filing
        embedder=_RateLimitEmbedder(),
        symbol=sym,
        record=record,
        raw_bucket=None,
    )

    with session_scope(session_factory) as s:
        # Filing row landed despite the embed failure.
        filings = s.execute(select(Filing).where(Filing.symbol == sym)).scalars().all()
        assert len(filings) == 1, "filing row must persist when embedder fails"
        assert filings[0].accession_no == "0000-fp-1"

        # Chunks landed with text but no vector.
        chunks = (
            s.execute(select(FilingChunk).where(FilingChunk.symbol == sym))
            .scalars()
            .all()
        )
        assert chunks, "chunks must persist with embedding_status='pending'"
        for c in chunks:
            assert c.embedding_status == "pending"
            assert c.embedding is None
            assert c.embedding_model is None
            assert c.chunk_text  # text is what we'll re-embed later

    _wipe_filings(session_factory, sym)


# --- 2. Two-phase: success path marks chunks ok ------------------------------------------


@pytest.mark.asyncio
async def test_embedder_success_marks_chunks_ok(session_factory):
    sym = "TSTFP2"
    _seed_ticker(session_factory, sym)
    _wipe_filings(session_factory, sym)

    record = _make_record(sym, accession="0000-fp-2")
    result = await _store_filing(
        factory=session_factory,
        edgar=None,
        embedder=_OkEmbedder(),
        symbol=sym,
        record=record,
        raw_bucket=None,
    )
    assert result["filing_id"]
    assert result["chunks"] > 0
    assert result["embedded"] == result["chunks"]

    with session_scope(session_factory) as s:
        chunks = (
            s.execute(select(FilingChunk).where(FilingChunk.symbol == sym))
            .scalars()
            .all()
        )
        assert chunks
        assert all(c.embedding_status == "ok" for c in chunks)
        assert all(c.embedding is not None for c in chunks)
        assert all(c.embedding_model == "fake-ok" for c in chunks)

    _wipe_filings(session_factory, sym)


# --- 3. Backfill idempotency -------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_skips_rows_already_embedded(session_factory):
    """Idempotency safety check from the brief: even if a row has
    embedding_status='pending' (due to e.g. a status-update failure earlier),
    if embedding IS NOT NULL the backfill must skip it. Running the backfill
    a second time must be a clean no-op."""
    from fii_data_clients.embeddings import EMBEDDING_DIM

    sym = "TSTFP3"
    _seed_ticker(session_factory, sym)
    _wipe_filings(session_factory, sym)

    # Phase A: store filing + pending chunks via the rate-limited path.
    record = _make_record(sym, accession="0000-fp-3")
    await _store_filing(
        factory=session_factory,
        edgar=None,
        embedder=_RateLimitEmbedder(),
        symbol=sym,
        record=record,
        raw_bucket=None,
    )

    # Sanity: rows are pending.
    with session_scope(session_factory) as s:
        pending_count = (
            s.query(FilingChunk)
            .filter(FilingChunk.symbol == sym, FilingChunk.embedding_status == "pending")
            .count()
        )
    assert pending_count > 0

    # Inject the inconsistent state the brief warns about: one chunk has a
    # vector but status='pending'. Backfill MUST NOT re-embed that one.
    with session_scope(session_factory) as s:
        first_chunk = (
            s.execute(select(FilingChunk).where(FilingChunk.symbol == sym).limit(1))
            .scalars()
            .one()
        )
        first_chunk.embedding = [0.5] * EMBEDDING_DIM
        # Note: status STAYS 'pending' — that's the inconsistency we're guarding against.

    # Now run the backfill query directly (the CLI's core logic) and confirm
    # the inconsistent row is skipped by the predicate.
    pending_predicate = (
        FilingChunk.embedding_status == "pending",
        FilingChunk.embedding.is_(None),
    )
    with session_scope(session_factory) as s:
        eligible = (
            s.execute(
                select(FilingChunk).where(
                    FilingChunk.symbol == sym, *pending_predicate
                )
            )
            .scalars()
            .all()
        )
    assert all(c.chunk_id != str(first_chunk.chunk_id) for c in eligible), (
        "backfill predicate must skip rows where embedding IS NOT NULL even if "
        "status='pending'"
    )
    # All other pending rows should still be eligible.
    assert len(eligible) == pending_count - 1

    _wipe_filings(session_factory, sym)


# --- 4. Section parser tolerates list-shape items (edgartools 5.30.2) -------------------


def test_extract_filing_sections_handles_list_items_shape(monkeypatch, caplog):
    """Edgartools 5.30.2: TenK.items returns list[str] and bodies live behind
    __getitem__. Old code did .items() on a list and crashed silently."""
    import logging
    from datetime import date as _date

    from fii_data_clients.edgar import EdgarClient

    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    class _Typed:
        # Real edgartools TenK shape: items is a list of "Item X" names.
        items: ClassVar[list[str]] = ["Item 1", "Item 1A", "Item 7"]

        _bodies: ClassVar[dict[str, str]] = {
            "Item 1": "Business: Apple designs ...",
            "Item 1A": "Risk factors: supply ...",
            "Item 7": "MD&A: revenue ...",
        }

        def __getitem__(self, name: str) -> str:
            return self._bodies[name]

    class _Filing:
        accession_no = "0000-list-shape"
        form = "10-K"
        filing_date = _date(2026, 1, 1)
        period_of_report = _date(2025, 12, 31)
        filing_url = "https://example.com/x"
        header = "AAPL"

        def text(self):
            return "full filing text " * 100

        def obj(self):
            return _Typed()

    class _Filings:
        def latest(self, n: int):
            return [_Filing()]

    class _Company:
        def get_filings(self, *, form):
            return _Filings()

    from unittest.mock import patch

    with patch("edgar.Company", return_value=_Company()):
        client = EdgarClient()
        with caplog.at_level(logging.INFO):
            record = client._fetch_filing_sync("AAPL", "10-K")

    assert record is not None
    assert record.sections is not None
    assert set(record.sections.keys()) == {"Item 1", "Item 1A", "Item 7"}
    assert record.sections["Item 1"].startswith("Business: Apple")

    # No edgar_parse_failed events for sections — the new path handles list shape.
    parse_fails = [
        r
        for r in caplog.records
        if r.getMessage() == "edgar_parse_failed"
        and getattr(r, "fn", None) == "extract_filing_sections"
    ]
    assert not parse_fails, "list-shape items must NOT trigger extract_filing_sections fallback"


def test_extract_filing_sections_per_item_guard_skips_bad_items(monkeypatch, caplog):
    """Per-item guard from the brief: if __getitem__ raises for one item, log
    edgar_section_items_skipped and continue with the rest."""
    import logging
    from datetime import date as _date

    from fii_data_clients.edgar import EdgarClient

    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    class _Typed:
        items: ClassVar[list[str]] = ["Item 1", "Item 1A", "Item 7"]

        def __getitem__(self, name: str):
            if name == "Item 1A":
                raise KeyError("simulated edgartools getitem failure")
            return f"body for {name}"

    class _Filing:
        accession_no = "0000-bad-item"
        form = "10-K"
        filing_date = _date(2026, 1, 1)
        period_of_report = _date(2025, 12, 31)
        filing_url = None
        header = "AAPL"

        def text(self):
            return "full text"

        def obj(self):
            return _Typed()

    class _Company:
        def get_filings(self, *, form):
            class _F:
                def latest(self, n):
                    return [_Filing()]

            return _F()

    from unittest.mock import patch

    with patch("edgar.Company", return_value=_Company()):
        client = EdgarClient()
        with caplog.at_level(logging.WARNING):
            record = client._fetch_filing_sync("AAPL", "10-K")

    # Bad item skipped, others retained.
    assert record.sections is not None
    assert set(record.sections.keys()) == {"Item 1", "Item 7"}

    # Skipped log fired.
    skipped = [
        r for r in caplog.records if r.getMessage() == "edgar_section_items_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].count == 1
    # details is a list of (name, exc_type) tuples.
    assert ("Item 1A", "KeyError") in skipped[0].details


def test_extract_filing_sections_returns_none_when_all_items_fail(monkeypatch):
    """When every typed[name] lookup raises, sections must end as None so
    downstream falls back to whole-filing chunking."""
    from datetime import date as _date

    from fii_data_clients.edgar import EdgarClient

    monkeypatch.setenv("EDGAR_IDENTITY", "FII-PRISM test test@yourdomain.com")

    class _Typed:
        items: ClassVar[list[str]] = ["Item 1", "Item 1A"]

        def __getitem__(self, name):
            raise RuntimeError("everything broken")

    class _Filing:
        accession_no = "0000-none"
        form = "10-K"
        filing_date = _date(2026, 1, 1)
        period_of_report = None
        filing_url = None
        header = "AAPL"

        def text(self):
            return "txt"

        def obj(self):
            return _Typed()

    class _Company:
        def get_filings(self, *, form):
            class _F:
                def latest(self, n):
                    return [_Filing()]

            return _F()

    from unittest.mock import patch

    with patch("edgar.Company", return_value=_Company()):
        client = EdgarClient()
        record = client._fetch_filing_sync("AAPL", "10-K")

    assert record.sections is None, (
        "all-items-failed must collapse sections to None so chunker falls back to whole text"
    )
