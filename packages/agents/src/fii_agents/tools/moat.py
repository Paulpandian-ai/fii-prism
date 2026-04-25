"""Tools for the Moat specialist.

The agent is required by its prompt to look for evidence the moat is ERODING first
(adversarial frame). The tools surface the same data either bull or bear could use —
the prompt enforces the order of inquiry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog
from fii_data_clients import Embedder
from fii_db import FundamentalsQuarterly, StatementType, Ticker
from fii_db.session import session_scope
from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from fii_agents.tools.fundamentals import (
    FundamentalsToolContext,
    _get_latest_10k,
    _query_filing_rag,
    _read_filing_section,
)

log = structlog.get_logger(__name__)


@dataclass
class MoatToolContext:
    factory: sessionmaker
    embedder: Embedder
    call_count: int = 0
    max_calls: int = 12


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_latest_10k",
        "description": "Filing metadata + section list for the most recent 10-K.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "read_filing_section",
        "description": "Full text of a section. Wrapped in <filing_text> tags (untrusted).",
        "input_schema": {
            "type": "object",
            "properties": {"filing_id": {"type": "string"}, "section_name": {"type": "string"}},
            "required": ["filing_id", "section_name"],
        },
    },
    {
        "name": "query_filing_rag",
        "description": "Semantic search across filing chunks (cosine similarity).",
        "input_schema": {
            "type": "object",
            "properties": {
                "filing_id": {"type": "string"},
                "question": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["filing_id", "question"],
        },
    },
    {
        "name": "get_gross_margin_history",
        "description": "Up to 10 years of gross margin (TTM-equivalent from quarterly ratios).",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "years": {"type": "integer", "default": 10},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_roic_vs_wacc_history",
        "description": "Up to 10 years of ROIC; pair against the agent's chosen WACC to test the moat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "years": {"type": "integer", "default": 10},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_industry_summary",
        "description": "Sector / industry / market-cap bucket for the symbol from the tickers table.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]


class ToolCallError(Exception):
    pass


async def dispatch(name: str, input_: dict[str, Any], ctx: MoatToolContext) -> dict[str, Any]:
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
            specialist="moat",
            symbol=input_.get("symbol") or input_.get("filing_id"),
            call_number=ctx.call_count,
            duration_ms=int((time.perf_counter() - start) * 1000),
            success=success,
        )


async def _route(name: str, input_: dict[str, Any], ctx: MoatToolContext) -> dict[str, Any]:
    # Reuse Fundamentals helpers for filing access.
    fctx = FundamentalsToolContext(factory=ctx.factory, embedder=ctx.embedder, raw_bucket=None)
    if name == "get_latest_10k":
        return _get_latest_10k(fctx, input_["symbol"])
    if name == "read_filing_section":
        return _read_filing_section(fctx, input_["filing_id"], input_["section_name"])
    if name == "query_filing_rag":
        return await _query_filing_rag(
            fctx, input_["filing_id"], input_["question"], int(input_.get("k") or 5)
        )
    if name == "get_gross_margin_history":
        return _get_metric_history(
            ctx, input_["symbol"], "gross_margin", int(input_.get("years") or 10)
        )
    if name == "get_roic_vs_wacc_history":
        return _get_metric_history(
            ctx, input_["symbol"], "return_on_invested_capital", int(input_.get("years") or 10)
        )
    if name == "get_industry_summary":
        return _get_industry_summary(ctx, input_["symbol"])
    raise ToolCallError(f"unknown tool: {name}")


def _get_metric_history(
    ctx: MoatToolContext, symbol: str, column: str, years: int
) -> dict[str, Any]:
    periods = years * 4
    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(FundamentalsQuarterly)
                .where(
                    FundamentalsQuarterly.symbol == symbol.upper(),
                    FundamentalsQuarterly.statement_type == StatementType.RATIOS.value,
                )
                .order_by(desc(FundamentalsQuarterly.fiscal_period_end))
                .limit(periods)
            )
            .scalars()
            .all()
        )
    series = []
    for r in rows:
        v = getattr(r, column)
        series.append(
            {
                "fiscal_period_end": r.fiscal_period_end.isoformat(),
                "value": float(v) if v is not None else None,
            }
        )
    return {"symbol": symbol.upper(), "metric": column, "count": len(series), "series": series}


def _get_industry_summary(ctx: MoatToolContext, symbol: str) -> dict[str, Any]:
    with session_scope(ctx.factory) as s:
        row = s.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
    if row is None:
        return {"error": f"ticker {symbol} not found"}
    return {
        "symbol": row.symbol,
        "name": row.name,
        "sector": row.sector,
        "industry": row.industry,
        "market_cap_bucket": row.market_cap_bucket,
    }
