"""Daily spend cap pre-flight check.

Sums today's `analyses.total_cost_usd` and rejects new runs once the rolling daily
total breaches the configured cap. Per-analysis hard cap continues to be enforced
inside the orchestrator's Budget object on each specialist call.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from fii_db import Analysis
from fii_db.session import session_scope
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker


def _daily_cap_usd() -> float:
    raw = os.environ.get("FII_DAILY_SPEND_CAP_USD", "20.0")
    try:
        return float(raw)
    except ValueError:
        return 20.0


def daily_spend_today(factory: sessionmaker) -> float:
    """Sum of total_cost_usd for analyses initiated since UTC midnight."""
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope(factory) as s:
        total = s.execute(
            select(func.coalesce(func.sum(Analysis.total_cost_usd), 0.0)).where(
                Analysis.initiated_at >= midnight
            )
        ).scalar_one()
    return float(total or 0.0)


def cap_status(factory: sessionmaker) -> dict[str, float | bool]:
    cap = _daily_cap_usd()
    spent = daily_spend_today(factory)
    return {
        "cap_usd": cap,
        "spent_today_usd": spent,
        "remaining_usd": max(0.0, cap - spent),
        "exceeded": spent >= cap,
        "midnight_resets_at": (
            datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ).isoformat(),
    }


def assert_within_daily_cap(factory: sessionmaker) -> None:
    """Raise RuntimeError if today's total_cost_usd already exceeds the configured cap."""
    status = cap_status(factory)
    if status["exceeded"]:
        raise DailyCapExceeded(
            f"daily spend cap of ${status['cap_usd']:.2f} reached "
            f"(${status['spent_today_usd']:.4f} so far). Resets at "
            f"{status['midnight_resets_at']}."
        )


class DailyCapExceeded(RuntimeError):
    """Raised by assert_within_daily_cap when the rolling daily cap is breached."""
