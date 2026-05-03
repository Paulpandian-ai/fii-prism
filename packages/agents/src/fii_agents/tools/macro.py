"""Tools for the Macro specialist.

Regime classification is deterministic — we read 3+ FRED series and apply explicit
rules. The agent then INTERPRETS the regime label in context, never overrides it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fii_db import MacroSeries
from fii_db.session import session_scope
from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from fii_agents.tools.shared import get_latest_macro_value, get_yield_curve

log = structlog.get_logger(__name__)

# Hard cap on FRED series included in any single tool response. Today no tool
# returns more than ~3 series (classify_regime returns yield_curve / unrate /
# nber_recession), so this is a forward-looking guard for any bulk-series
# helper added later.
MAX_MACRO_SERIES_PER_RESPONSE = 8


@dataclass
class MacroToolContext:
    factory: sessionmaker
    call_count: int = 0
    max_calls: int = 10


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_fred_latest",
        "description": "Latest observation for a FRED series id (e.g., DGS10, UNRATE, CPILFESL).",
        "input_schema": {
            "type": "object",
            "properties": {"series_id": {"type": "string"}},
            "required": ["series_id"],
        },
    },
    {
        "name": "get_fred_trailing",
        "description": "Trailing N months of monthly observations for a FRED series.",
        "input_schema": {
            "type": "object",
            "properties": {
                "series_id": {"type": "string"},
                "months": {"type": "integer", "minimum": 1, "maximum": 120, "default": 24},
            },
            "required": ["series_id"],
        },
    },
    {
        "name": "get_yield_curve",
        "description": "2y, 10y, and the 10y-2y spread (negative = inverted).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "classify_regime",
        "description": (
            "Deterministic regime label based on yield curve, unemployment trend, and the "
            "official NBER recession indicator. Returns one of expansion/late_cycle/recession/recovery."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


class ToolCallError(Exception):
    pass


async def dispatch(name: str, input_: dict[str, Any], ctx: MacroToolContext) -> dict[str, Any]:
    ctx.call_count += 1
    if ctx.call_count > ctx.max_calls:
        raise ToolCallError(f"Tool-call budget exhausted ({ctx.max_calls})")
    start = time.perf_counter()
    success = True
    try:
        return await _route(name, input_, ctx)
    except Exception as exc:
        success = False
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        log.info(
            "tool_call",
            tool=name,
            specialist="macro",
            series_id=input_.get("series_id"),
            call_number=ctx.call_count,
            duration_ms=int((time.perf_counter() - start) * 1000),
            success=success,
        )


async def _route(name: str, input_: dict[str, Any], ctx: MacroToolContext) -> dict[str, Any]:
    if name == "get_fred_latest":
        return get_latest_macro_value(ctx.factory, input_["series_id"])
    if name == "get_fred_trailing":
        return _get_trailing(ctx, input_["series_id"], int(input_.get("months") or 24))
    if name == "get_yield_curve":
        return get_yield_curve(ctx.factory)
    if name == "classify_regime":
        return _classify_regime(ctx)
    raise ToolCallError(f"unknown tool: {name}")


def _get_trailing(ctx: MacroToolContext, series_id: str, months: int) -> dict[str, Any]:
    cutoff = date.today() - timedelta(days=months * 31)
    with session_scope(ctx.factory) as s:
        rows = s.execute(
            select(MacroSeries.observation_date, MacroSeries.value)
            .where(MacroSeries.series_id == series_id, MacroSeries.observation_date >= cutoff)
            .order_by(desc(MacroSeries.observation_date))
        ).all()
    if not rows:
        return {
            "series_id": series_id,
            "count": 0,
            "note": "not yet ingested; run `fii-ingest macro`",
        }
    obs = [
        {
            "date": r[0].isoformat(),
            "value": float(r[1])
            if isinstance(r[1], Decimal)
            else (None if r[1] is None else float(r[1])),
        }
        for r in rows
    ]
    return {"series_id": series_id, "count": len(obs), "observations": obs}


def _classify_regime(ctx: MacroToolContext) -> dict[str, Any]:
    yc = get_yield_curve(ctx.factory)
    unrate = get_latest_macro_value(ctx.factory, "UNRATE")
    nber = get_latest_macro_value(ctx.factory, "USRECD")
    inverted = yc.get("inverted")
    rec = (nber.get("value") or 0) >= 1
    label: str
    rationale: list[str] = []

    if rec:
        label = "recession"
        rationale.append("USRECD == 1 (NBER recession indicator)")
    elif inverted is True:
        label = "late_cycle"
        rationale.append("yield curve is inverted (10y < 2y)")
    elif unrate.get("value") is not None and unrate["value"] >= 5.0:
        label = "recovery"
        rationale.append(f"UNRATE elevated at {unrate['value']}")
    else:
        label = "expansion"
        rationale.append("no inversion and unemployment is healthy")

    return {
        "regime": label,
        "rationale": rationale,
        "inputs": {"yield_curve": yc, "unrate": unrate, "nber_recession": nber},
        "note": "This is a deterministic best-effort label; the agent interprets in context.",
    }
