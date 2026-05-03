"""Section: bulk-upsert chunking guard against Postgres' 65,535-parameter limit.

Hits a real Postgres (the conftest's session_factory fixture, not a mock) with
100,000 synthetic macro_series rows and confirms the chunked path completes without
the `number of parameters must be between 0 and 65535` error.

A naive `pg_insert(MacroSeries).values(rows)` for 100k rows x 4 columns = 400k
parameters would fail immediately. The chunked path bounds each statement to
BULK_INSERT_CHUNK_SIZE (500) x 4 columns = 2,000 parameters per statement.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fii_db import MacroSeries
from fii_db.session import session_scope
from fii_ingest.bulk import BULK_INSERT_CHUNK_SIZE, chunked_upsert
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


def test_chunked_upsert_handles_100k_macro_rows(session_factory):
    """Real-Postgres exercise. Builds 100,000 rows (4 columns each = 400,000 params if
    sent as one statement, ~6x the protocol limit) and confirms chunking succeeds."""
    series_id = "ZZBULK_TEST"
    rows: list[dict] = []
    base = date(1900, 1, 1)
    for i in range(100_000):
        rows.append(
            {
                "series_id": series_id,
                "observation_date": base + timedelta(days=i),
                "value": Decimal(str(i)),
                "release_id": None,
            }
        )

    def _build(chunk):
        stmt = pg_insert(MacroSeries).values(chunk)
        return stmt.on_conflict_do_update(
            index_elements=[MacroSeries.series_id, MacroSeries.observation_date],
            set_={"value": stmt.excluded.value},
        )

    try:
        with session_scope(session_factory) as s:
            written = chunked_upsert(s, rows, build_stmt=_build, label="bulk_test_macro")
        assert written == 100_000

        # Confirm Postgres actually has all 100k rows.
        with session_scope(session_factory) as s:
            n = s.execute(
                select(func.count())
                .select_from(MacroSeries)
                .where(MacroSeries.series_id == series_id)
            ).scalar_one()
        assert n == 100_000, f"expected 100_000 rows for {series_id}; got {n}"

        # Idempotency: a second run on the same rows should still succeed via
        # ON CONFLICT DO UPDATE (no integrity-error storm) and leave the count unchanged.
        with session_scope(session_factory) as s:
            chunked_upsert(s, rows, build_stmt=_build, label="bulk_test_macro_redo")
        with session_scope(session_factory) as s:
            n = s.execute(
                select(func.count())
                .select_from(MacroSeries)
                .where(MacroSeries.series_id == series_id)
            ).scalar_one()
        assert n == 100_000
    finally:
        with session_scope(session_factory) as s:
            s.execute(delete(MacroSeries).where(MacroSeries.series_id == series_id))


def test_chunk_size_keeps_us_under_postgres_limit():
    """Sanity: 500 rows x any reasonable column count must stay under 65,535 params.
    The widest table we touch (filing_chunks) has 8 inserted columns."""
    widest_columns = 12  # generous upper bound across all our tables
    assert BULK_INSERT_CHUNK_SIZE * widest_columns < 65_535


def test_chunked_upsert_empty_returns_zero(session_factory):
    """Empty input is a no-op; we must NOT execute an empty statement."""
    calls = []

    def _build(chunk):
        calls.append(len(chunk))
        return pg_insert(MacroSeries).values(chunk)

    with session_scope(session_factory) as s:
        n = chunked_upsert(s, [], build_stmt=_build)
    assert n == 0
    assert calls == []
