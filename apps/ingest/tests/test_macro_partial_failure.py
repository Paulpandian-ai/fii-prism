"""Per-series transaction isolation in macro ingestion.

If one FRED series raises (e.g., HTTP 400 for a discontinued series like
GOLDAMGBD228NLBM), the surrounding `ingest_all_default` MUST commit the
series that succeeded and surface the failed one in the report — the
previous all-in-one transaction lost a 93K-row corpus when a single 400 hit.
"""

from __future__ import annotations

import pytest
from fii_db import MacroSeries
from fii_db.session import session_scope
from fii_ingest.jobs.macro import ingest_all_default
from sqlalchemy import delete, select


class _FakeFred:
    """Stand-in for FredClient.get_series. Returns canned observations for the
    happy-path series; raises for the configured "broken" series."""

    def __init__(self, broken: set[str]) -> None:
        self.broken = broken
        self.calls: list[str] = []

    async def get_series(self, series_id: str, *args, **kwargs) -> list[dict]:
        self.calls.append(series_id)
        if series_id in self.broken:
            # Mirror the real-world failure: FRED returns 400 for discontinued
            # series, and the client raises ProviderError → bubbles up here.
            raise RuntimeError(f"fred 400: series {series_id} not found")
        # Three observations, one per day, real-shaped.
        return [
            {"date": "2026-05-01", "value": "1.0"},
            {"date": "2026-05-02", "value": "1.5"},
            {"date": "2026-05-03", "value": "2.0"},
        ]


@pytest.mark.asyncio
async def test_partial_failure_isolates_per_series(session_factory):
    """One series raises; the other two should still commit and the failure is
    reported in `result.failed` rather than aborting the loop."""
    series_ids = ("PFTEST_A", "PFTEST_BROKEN", "PFTEST_C")
    broken = {"PFTEST_BROKEN"}

    # Clean up any prior runs.
    with session_scope(session_factory) as s:
        s.execute(delete(MacroSeries).where(MacroSeries.series_id.in_(series_ids)))

    fred = _FakeFred(broken=broken)
    result = await ingest_all_default(session_factory, fred=fred, series_ids=series_ids)

    # All three series were attempted (loop didn't short-circuit on the failure).
    assert fred.calls == list(series_ids)

    # The two healthy series committed (3 rows each = 6 total).
    assert result.total_rows == 6, f"expected 6 rows, got {result.total_rows}"
    assert len(result.failed) == 1
    assert result.failed[0][0] == "PFTEST_BROKEN"
    assert "fred 400" in result.failed[0][1]

    # Verify the rows are actually in the DB — proves the failure didn't roll
    # back the healthy commits.
    with session_scope(session_factory) as s:
        a_rows = s.execute(
            select(MacroSeries).where(MacroSeries.series_id == "PFTEST_A")
        ).scalars().all()
        broken_rows = s.execute(
            select(MacroSeries).where(MacroSeries.series_id == "PFTEST_BROKEN")
        ).scalars().all()
        c_rows = s.execute(
            select(MacroSeries).where(MacroSeries.series_id == "PFTEST_C")
        ).scalars().all()
        assert len(a_rows) == 3, "PFTEST_A rows must persist despite later failure"
        assert len(broken_rows) == 0, "PFTEST_BROKEN rows must not be present"
        assert len(c_rows) == 3, "PFTEST_C rows must persist despite earlier failure"

    # Cleanup.
    with session_scope(session_factory) as s:
        s.execute(delete(MacroSeries).where(MacroSeries.series_id.in_(series_ids)))


def test_default_series_no_longer_includes_discontinued_gold():
    """The discontinued GOLDAMGBD228NLBM series is replaced by the still-active
    GOLDPMGBD228NLBM (London PM fix). Documenting this with a tight assertion
    so a regression that re-adds the dead series shows up in tests."""
    from fii_data_clients import DEFAULT_MACRO_SERIES

    assert "GOLDAMGBD228NLBM" not in DEFAULT_MACRO_SERIES, (
        "GOLDAMGBD228NLBM was discontinued by FRED; remove it from defaults"
    )
    # We keep at least one gold series in the default catalog.
    assert any("GOLD" in s for s in DEFAULT_MACRO_SERIES), (
        "expected a gold series (GOLDPMGBD228NLBM) in DEFAULT_MACRO_SERIES"
    )


@pytest.mark.asyncio
async def test_happy_path_no_failures_commits_every_series(session_factory):
    """Sanity check that ingest_all_default with an empty broken set commits
    every series cleanly (no partial-failure regression in the happy path)."""
    series_ids = ("PFOK_A", "PFOK_B")
    with session_scope(session_factory) as s:
        s.execute(delete(MacroSeries).where(MacroSeries.series_id.in_(series_ids)))

    fred = _FakeFred(broken=set())
    result = await ingest_all_default(session_factory, fred=fred, series_ids=series_ids)
    assert result.failed == []
    assert result.total_rows == 6  # 2 series x 3 rows each

    with session_scope(session_factory) as s:
        s.execute(delete(MacroSeries).where(MacroSeries.series_id.in_(series_ids)))
