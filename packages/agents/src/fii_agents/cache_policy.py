"""Per-specialist cache freshness + per-run safety caps.

Two concerns colocated here so the freshness gate and the run-time caps share a
single source of truth:

1. **TTLs** govern when a cached output in `specialist_cache` is still considered
   fresh. `is_fresh(name, last_run_at)` returns True iff `now - last_run_at < TTL`.

2. **Run caps** bound a single specialist's resource consumption per run. A
   specialist that exceeds either its call cap or its cost cap is aborted with
   `status="aborted_cap"`; whatever output it managed to produce is persisted so
   downstream synthesis can still see partial work (and refuse to synthesize on
   it via the freshness gate, which treats `aborted_cap` as not-fresh).

Bull/bear/synthesis are deliberately absent from this map — they run inside
synthesis as part of a tiny LangGraph and are exempt from the per-specialist cap
(synthesis has its own 3-attempt JSON-validity cap; see `synthesis.py`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

# --- Cacheable specialist identifiers -----------------------------------------------------

# String identifiers (LangGraph state keys / API path segments). These are NOT the
# fii_db.SpecialistName enum values 1:1 — the LangGraph node `news_sentiment` is
# `news` in the enum, `insider_flow` is `insider`. We use these stable string IDs
# at the cache + API layer.
CACHEABLE_SPECIALISTS: tuple[str, ...] = (
    "fundamentals",
    "valuation",
    "moat",
    "macro",
    "technical",
    "news",
    "insider",
    "risk",
)


# LangGraph node-name → cache-policy key. Some specialists use a longer node name
# (`news_sentiment`, `insider_flow`, `risk_preliminary`) but cache + caps are keyed
# on the short canonical ID. Identity-mapped names (fundamentals, valuation, ...)
# fall through unchanged.
_NODE_NAME_ALIASES: Mapping[str, str] = {
    "news_sentiment": "news",
    "insider_flow": "insider",
    "risk_preliminary": "risk",
    "risk_final": "risk",
}


def canonical_name(name: str) -> str:
    return _NODE_NAME_ALIASES.get(name, name)


# --- Default TTLs ------------------------------------------------------------------------

_DEFAULT_TTL_SECONDS: Mapping[str, int] = {
    "fundamentals": 30 * 24 * 3600,  # 30 days
    "moat": 90 * 24 * 3600,  # 90 days
    "valuation": 14 * 24 * 3600,  # 14 days
    "macro": 1 * 24 * 3600,  # 1 day — regime can shift fast
    "technical": 4 * 3600,  # 4 hours — intraday-sensitive
    "news": 4 * 3600,  # 4 hours
    "insider": 7 * 24 * 3600,  # 7 days
    "risk": 1 * 24 * 3600,  # 1 day
}


def ttl_seconds(name: str) -> int:
    """Return the TTL for a specialist. Override via FII_TTL_<NAME>_SEC env var."""
    name = canonical_name(name)
    env = os.environ.get(f"FII_TTL_{name.upper()}_SEC")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return _DEFAULT_TTL_SECONDS.get(name, 24 * 3600)


def is_fresh(name: str, last_run_at: datetime | None, *, status: str = "ok") -> bool:
    """A cached row is fresh iff status=='ok' AND last_run_at is within the TTL.

    aborted_cap / synthesis_invalid rows are NEVER fresh — they exist as audit
    trail but synthesis must refuse to use them.
    """
    if last_run_at is None or status != "ok":
        return False
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - last_run_at < timedelta(seconds=ttl_seconds(name))


def expires_at(name: str, last_run_at: datetime) -> datetime:
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=UTC)
    return last_run_at + timedelta(seconds=ttl_seconds(name))


# --- Per-specialist run caps -------------------------------------------------------------

DEFAULT_PER_SPECIALIST_CALL_CAP = 25
DEFAULT_PER_SPECIALIST_COST_CAP_USD = 0.25


# Per-specialist hard cost ceiling. Tiers reflect the heaviness of each
# specialist's reasoning + data: Fundamentals reads filings + 4 statement types,
# so it gets the widest budget. Moat/Valuation are mid-weight (RAG-heavy and
# multi-DCF respectively). News/Macro/Technical/Insider/Risk are quick-look
# specialists. Synthesis is listed for completeness — it has its own attempt
# cap (synthesis_max_attempts), but the cost lookup is here so future
# enforcement can read from the same source.
_DEFAULT_COST_CAP_USD: Mapping[str, float] = {
    "fundamentals": 0.60,
    "moat": 0.40,
    "valuation": 0.40,
    "synthesis": 0.40,
    "news": 0.25,
    "macro": 0.25,
    "technical": 0.25,
    "insider": 0.25,
    "risk": 0.25,
}


def call_cap(name: str) -> int:
    name = canonical_name(name)
    env = os.environ.get(f"FII_CAP_CALLS_{name.upper()}")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return int(os.environ.get("FII_CAP_CALLS_DEFAULT", DEFAULT_PER_SPECIALIST_CALL_CAP))


def cost_cap_usd(name: str) -> float:
    """Per-specialist hard cost ceiling.

    Lookup precedence:
      1. ``FII_COST_CAP_<NAME>`` env var (preferred)
      2. ``FII_CAP_COST_USD_<NAME>`` env var (legacy alias from Phase 1)
      3. Per-name default in ``_DEFAULT_COST_CAP_USD``
      4. ``FII_COST_CAP_DEFAULT`` / ``FII_CAP_COST_USD_DEFAULT`` env vars
      5. Hardcoded ``DEFAULT_PER_SPECIALIST_COST_CAP_USD`` ($0.25)
    """
    name = canonical_name(name)
    for env_name in (f"FII_COST_CAP_{name.upper()}", f"FII_CAP_COST_USD_{name.upper()}"):
        env = os.environ.get(env_name)
        if env:
            try:
                return float(env)
            except ValueError:
                pass
    if name in _DEFAULT_COST_CAP_USD:
        return _DEFAULT_COST_CAP_USD[name]
    fallback = os.environ.get(
        "FII_COST_CAP_DEFAULT",
        os.environ.get(
            "FII_CAP_COST_USD_DEFAULT", str(DEFAULT_PER_SPECIALIST_COST_CAP_USD)
        ),
    )
    try:
        return float(fallback)
    except ValueError:
        return DEFAULT_PER_SPECIALIST_COST_CAP_USD


# --- In-prompt soft tool-call budget -----------------------------------------------------
#
# This is a SOFT hint baked into each specialist's system prompt — it tells Claude
# how many tool calls to plan for. The Phase-1 hard cap (call_cap / cost_cap_usd
# above) still bounds runaway behavior. Fundamentals has the most tools (FMP +
# EDGAR + RAG + ratio calculators) and burns calls fastest, so it gets a wider
# soft budget by default. Override per-specialist via FII_TOOL_BUDGET_<NAME>.

_DEFAULT_TOOL_CALL_SOFT_BUDGET: Mapping[str, int] = {
    "fundamentals": 22,
}
_DEFAULT_TOOL_CALL_SOFT_BUDGET_OTHER = 15


def tool_call_soft_budget(name: str) -> int:
    """Return the in-prompt tool-call budget for a specialist.

    Defaults: 22 for fundamentals (which has the most tools), 15 for the rest.
    Override via FII_TOOL_BUDGET_<NAME> env var.
    """
    name = canonical_name(name)
    env = os.environ.get(f"FII_TOOL_BUDGET_{name.upper()}")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return _DEFAULT_TOOL_CALL_SOFT_BUDGET.get(name, _DEFAULT_TOOL_CALL_SOFT_BUDGET_OTHER)


# --- Synthesis-specific JSON-validity cap ------------------------------------------------

DEFAULT_SYNTHESIS_MAX_ATTEMPTS = 3


def synthesis_max_attempts() -> int:
    env = os.environ.get("FII_SYNTHESIS_MAX_ATTEMPTS")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_SYNTHESIS_MAX_ATTEMPTS
