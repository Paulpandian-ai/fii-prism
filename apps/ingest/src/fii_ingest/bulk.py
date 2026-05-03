"""Chunked bulk-upsert helper.

Postgres has a hard limit of 65,535 bind parameters per single SQL statement (uint16
in the protocol's parameter-count field). A naive `pg_insert(Table).values(rows)` for
a wide table + many rows trips this — the most-affected case is `macro_series` where
~17,000 daily observations x 4 columns ≈ 68,000 parameters > 65,535.

Use `chunked_upsert` from every ingest job that builds an INSERT from a list whose
size isn't tightly bounded. The chunks run inside the caller's transaction (we never
commit; we only `session.execute()`), so a failure in chunk N rolls back chunks 1..N
together via the outer `session_scope`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

# 500 rows x max ~10 columns per row = 5,000 parameters per statement, well under
# Postgres' 65,535 hard cap with comfortable headroom for wider tables.
BULK_INSERT_CHUNK_SIZE = 500


def chunked_upsert(
    session: Session,
    rows: list[dict[str, Any]],
    *,
    build_stmt: Callable[[list[dict[str, Any]]], Insert],
    chunk_size: int = BULK_INSERT_CHUNK_SIZE,
    label: str | None = None,
) -> int:
    """Execute an upsert in `chunk_size`-row batches against the same session.

    `build_stmt` is a callable that takes a chunk of rows and returns the fully
    configured `Insert` statement (with `.values(chunk)` and any
    `.on_conflict_do_update(...)` already attached). The caller defines the conflict
    behavior; this helper only handles the chunking.

    Returns the total row count submitted. Logs `bulk_upsert_chunk` per chunk so you
    can see progress on long inserts.

    Example:
        def _build(chunk):
            stmt = pg_insert(MacroSeries).values(chunk)
            return stmt.on_conflict_do_update(
                index_elements=[MacroSeries.series_id, MacroSeries.observation_date],
                set_={"value": stmt.excluded.value},
            )
        chunked_upsert(session, rows, build_stmt=_build, label="macro_series")
    """
    if not rows:
        return 0
    total = len(rows)
    for i in range(0, total, chunk_size):
        chunk = rows[i : i + chunk_size]
        session.execute(build_stmt(chunk))
        log.info(
            "bulk_upsert_chunk",
            label=label,
            chunk_index=i // chunk_size,
            chunk_size=len(chunk),
            total=total,
        )
    return total
