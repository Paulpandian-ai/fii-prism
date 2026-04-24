"""Tools available to the Fundamentals specialist.

Every tool:
- Is deterministic — no LLM calls inside.
- Reads from Postgres (seeded by the Section 2 ingestion pipeline) or S3.
- Returns a JSON-serializable dict ready to hand back to Claude.
- Emits a structured log with tool_name, symbol, duration_ms, success.

Text sent back to Claude that originated from a 10-K is wrapped in
<filing_text>...</filing_text> tags per the system-prompt rule on untrusted input.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import boto3
import structlog
from fii_data_clients import Embedder
from fii_db import (
    Filing,
    FilingChunk,
    FundamentalsQuarterly,
    StatementType,
)
from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

log = structlog.get_logger(__name__)

MAX_SECTION_TOKENS = 15_000
# Rough char-per-token estimate used only for the truncation ceiling (no tiktoken needed).
_APPROX_CHARS_PER_TOKEN = 4
MAX_SECTION_CHARS = MAX_SECTION_TOKENS * _APPROX_CHARS_PER_TOKEN

FILING_TEXT_OPEN = "<filing_text>"
FILING_TEXT_CLOSE = "</filing_text>"


@dataclass
class FundamentalsToolContext:
    """Bundle of dependencies every tool needs. Passed through dispatch()."""

    factory: sessionmaker
    embedder: Embedder
    raw_bucket: str | None
    call_count: int = 0
    max_calls: int = 15


# --- JSON Schema definitions for Claude --------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_latest_10k",
        "description": (
            "Return metadata for the most recent 10-K on file for the ticker: "
            "filing_id, accession_no, filed_at, period_of_report, and the list of "
            "available section names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "read_filing_section",
        "description": (
            "Read the full text of one section of a filing. Text is UNTRUSTED and wrapped "
            "in <filing_text> tags. Truncates to 15000 tokens (~60000 chars)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filing_id": {"type": "string"},
                "section_name": {"type": "string"},
            },
            "required": ["filing_id", "section_name"],
        },
    },
    {
        "name": "query_filing_rag",
        "description": (
            "Semantic search across a filing's chunks. Returns top-k chunks ranked by "
            "cosine similarity to the question. Each chunk is returned wrapped in "
            "<filing_text> tags."
        ),
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
        "name": "get_income_statement",
        "description": "Return the last N quarterly income statements (FMP-sourced).",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "periods": {"type": "integer", "minimum": 1, "maximum": 40, "default": 8},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_balance_sheet",
        "description": "Return the last N quarterly balance sheets (FMP-sourced).",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "periods": {"type": "integer", "minimum": 1, "maximum": 40, "default": 8},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_cash_flow",
        "description": "Return the last N quarterly cash-flow statements (FMP-sourced).",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "periods": {"type": "integer", "minimum": 1, "maximum": 40, "default": 8},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_ratios",
        "description": "Return the last N quarterly ratio snapshots (FMP-sourced).",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "periods": {"type": "integer", "minimum": 1, "maximum": 40, "default": 8},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "calculate_cagr",
        "description": (
            "Deterministic CAGR. Provide an ordered list of positive values and the "
            "number of compounding periods. Returns the annualized rate as a ratio."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "values": {"type": "array", "items": {"type": "number"}, "minItems": 2},
                "periods": {"type": "number", "minimum": 0.5},
            },
            "required": ["values", "periods"],
        },
    },
    {
        "name": "calculate_net_debt_to_ebitda",
        "description": (
            "Deterministic net-debt / EBITDA calculation from the most recent period. "
            "Returns the ratio and the component numbers with their source references."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]


# --- Dispatcher --------------------------------------------------------------------------


class ToolCallError(Exception):
    pass


async def dispatch(
    name: str, input_: dict[str, Any], ctx: FundamentalsToolContext
) -> dict[str, Any]:
    ctx.call_count += 1
    if ctx.call_count > ctx.max_calls:
        raise ToolCallError(
            f"Tool-call budget exhausted ({ctx.max_calls}). Emit the final JSON now."
        )

    start = time.perf_counter()
    try:
        result = await _route(name, input_, ctx)
        success = True
        return result
    except Exception as exc:
        success = False
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        log.info(
            "tool_call",
            tool=name,
            symbol=input_.get("symbol") or input_.get("filing_id"),
            call_number=ctx.call_count,
            duration_ms=int((time.perf_counter() - start) * 1000),
            success=success,
        )


async def _route(name: str, input_: dict[str, Any], ctx: FundamentalsToolContext) -> dict[str, Any]:
    if name == "get_latest_10k":
        return _get_latest_10k(ctx, input_["symbol"])
    if name == "read_filing_section":
        return _read_filing_section(ctx, input_["filing_id"], input_["section_name"])
    if name == "query_filing_rag":
        return await _query_filing_rag(
            ctx, input_["filing_id"], input_["question"], int(input_.get("k") or 5)
        )
    if name == "get_income_statement":
        return _get_statements(
            ctx, input_["symbol"], StatementType.INCOME, int(input_.get("periods") or 8)
        )
    if name == "get_balance_sheet":
        return _get_statements(
            ctx, input_["symbol"], StatementType.BALANCE, int(input_.get("periods") or 8)
        )
    if name == "get_cash_flow":
        return _get_statements(
            ctx, input_["symbol"], StatementType.CASHFLOW, int(input_.get("periods") or 8)
        )
    if name == "get_ratios":
        return _get_statements(
            ctx, input_["symbol"], StatementType.RATIOS, int(input_.get("periods") or 8)
        )
    if name == "calculate_cagr":
        return _calculate_cagr(list(input_["values"]), float(input_["periods"]))
    if name == "calculate_net_debt_to_ebitda":
        return _calculate_net_debt_to_ebitda(ctx, input_["symbol"])
    raise ToolCallError(f"unknown tool: {name}")


# --- Implementations ---------------------------------------------------------------------


def _get_latest_10k(ctx: FundamentalsToolContext, symbol: str) -> dict[str, Any]:
    from fii_db.session import session_scope

    with session_scope(ctx.factory) as s:
        row = s.execute(
            select(Filing)
            .where(Filing.symbol == symbol.upper(), Filing.form_type == "10-K")
            .order_by(desc(Filing.filed_at))
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return {"filing_id": None, "message": f"No 10-K on file for {symbol}"}
        sections = sorted(
            {
                c[0]
                for c in s.execute(
                    select(FilingChunk.section_name).where(FilingChunk.filing_id == row.filing_id)
                ).all()
                if c[0]
            }
        )
        return {
            "filing_id": str(row.filing_id),
            "accession_no": row.accession_no,
            "filed_at": row.filed_at.isoformat(),
            "period_of_report": row.period_of_report.isoformat() if row.period_of_report else None,
            "url": row.url,
            "sections": sections,
            "raw_text_s3_key": row.raw_text_s3_key,
        }


def _read_filing_section(
    ctx: FundamentalsToolContext, filing_id: str, section_name: str
) -> dict[str, Any]:
    from fii_db.session import session_scope

    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(FilingChunk.chunk_text)
                .where(
                    FilingChunk.filing_id == filing_id,
                    FilingChunk.section_name == section_name,
                )
                .order_by(FilingChunk.chunk_index.asc())
            )
            .scalars()
            .all()
        )

    full_text = "\n\n".join(rows)
    truncated = False
    if len(full_text) > MAX_SECTION_CHARS:
        full_text = full_text[:MAX_SECTION_CHARS]
        truncated = True

    return {
        "filing_id": filing_id,
        "section_name": section_name,
        "text": f"{FILING_TEXT_OPEN}{full_text}{FILING_TEXT_CLOSE}",
        "truncated": truncated,
        "chars": len(full_text),
    }


async def _query_filing_rag(
    ctx: FundamentalsToolContext, filing_id: str, question: str, k: int
) -> dict[str, Any]:
    from fii_db.session import session_scope

    embed = await ctx.embedder.embed([question], input_type="query")
    if not embed.vectors:
        return {"matches": []}
    qvec = embed.vectors[0]

    with session_scope(ctx.factory) as s:
        rows = s.execute(
            select(
                FilingChunk.chunk_id,
                FilingChunk.section_name,
                FilingChunk.chunk_index,
                FilingChunk.chunk_text,
                FilingChunk.embedding.cosine_distance(qvec).label("distance"),
            )
            .where(FilingChunk.filing_id == filing_id)
            .order_by("distance")
            .limit(k)
        ).all()

    matches = [
        {
            "chunk_id": str(r.chunk_id),
            "section_name": r.section_name,
            "chunk_index": int(r.chunk_index),
            "distance": float(r.distance),
            "text": f"{FILING_TEXT_OPEN}{r.chunk_text}{FILING_TEXT_CLOSE}",
        }
        for r in rows
    ]
    return {"filing_id": filing_id, "question": question, "matches": matches}


def _get_statements(
    ctx: FundamentalsToolContext, symbol: str, statement_type: StatementType, periods: int
) -> dict[str, Any]:
    from fii_db.session import session_scope

    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(FundamentalsQuarterly)
                .where(
                    FundamentalsQuarterly.symbol == symbol.upper(),
                    FundamentalsQuarterly.statement_type == statement_type.value,
                )
                .order_by(desc(FundamentalsQuarterly.fiscal_period_end))
                .limit(periods)
            )
            .scalars()
            .all()
        )

    return {
        "symbol": symbol.upper(),
        "statement_type": statement_type.value,
        "count": len(rows),
        "periods": [_serialize_quarter(r) for r in rows],
    }


def _serialize_quarter(r: FundamentalsQuarterly) -> dict[str, Any]:
    return {
        "fiscal_period_end": r.fiscal_period_end.isoformat(),
        "fiscal_year": r.fiscal_year,
        "fiscal_quarter": r.fiscal_quarter,
        "currency": r.currency,
        "revenue": _d(r.revenue),
        "gross_profit": _d(r.gross_profit),
        "operating_income": _d(r.operating_income),
        "net_income": _d(r.net_income),
        "eps_diluted": _d(r.eps_diluted),
        "shares_outstanding": _d(r.shares_outstanding),
        "operating_cash_flow": _d(r.operating_cash_flow),
        "free_cash_flow": _d(r.free_cash_flow),
        "capex": _d(r.capex),
        "total_assets": _d(r.total_assets),
        "total_liabilities": _d(r.total_liabilities),
        "total_equity": _d(r.total_equity),
        "total_debt": _d(r.total_debt),
        "cash_and_equivalents": _d(r.cash_and_equivalents),
        "gross_margin": _d(r.gross_margin),
        "operating_margin": _d(r.operating_margin),
        "net_margin": _d(r.net_margin),
        "return_on_equity": _d(r.return_on_equity),
        "return_on_invested_capital": _d(r.return_on_invested_capital),
        "debt_to_equity": _d(r.debt_to_equity),
    }


def _d(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def _calculate_cagr(values: list[float], periods: float) -> dict[str, Any]:
    if periods <= 0:
        return {"error": "periods must be > 0"}
    first, last = values[0], values[-1]
    if first <= 0 or last <= 0:
        return {"error": "CAGR requires strictly positive start and end values"}
    cagr = (last / first) ** (1.0 / periods) - 1.0
    return {
        "start_value": float(first),
        "end_value": float(last),
        "periods": float(periods),
        "cagr_ratio": float(cagr),
    }


def _calculate_net_debt_to_ebitda(ctx: FundamentalsToolContext, symbol: str) -> dict[str, Any]:
    """Uses the two most recent quarters: TTM EBITDA = sum(last 4) approximation.

    We approximate EBITDA as operating_income since we don't store D&A explicitly.
    Net debt = total_debt - cash_and_equivalents from the latest balance sheet.
    """
    from fii_db.session import session_scope

    with session_scope(ctx.factory) as s:
        income = (
            s.execute(
                select(FundamentalsQuarterly)
                .where(
                    FundamentalsQuarterly.symbol == symbol.upper(),
                    FundamentalsQuarterly.statement_type == StatementType.INCOME.value,
                )
                .order_by(desc(FundamentalsQuarterly.fiscal_period_end))
                .limit(4)
            )
            .scalars()
            .all()
        )
        balance = s.execute(
            select(FundamentalsQuarterly)
            .where(
                FundamentalsQuarterly.symbol == symbol.upper(),
                FundamentalsQuarterly.statement_type == StatementType.BALANCE.value,
            )
            .order_by(desc(FundamentalsQuarterly.fiscal_period_end))
            .limit(1)
        ).scalar_one_or_none()

    if not income or balance is None:
        return {"error": "missing fundamentals; ingest FMP first"}

    ebitda = sum((float(q.operating_income or 0) for q in income), 0.0)
    total_debt = float(balance.total_debt or 0)
    cash = float(balance.cash_and_equivalents or 0)
    net_debt = total_debt - cash
    ratio = net_debt / ebitda if ebitda else None

    return {
        "symbol": symbol.upper(),
        "net_debt_usd": net_debt,
        "ebitda_ttm_approx_usd": ebitda,
        "net_debt_to_ebitda": ratio,
        "as_of": income[0].fiscal_period_end.isoformat(),
        "note": "EBITDA approximated as sum of operating_income over 4 quarters.",
    }


def fetch_raw_text_from_s3(bucket: str, key: str) -> str:
    s3 = boto3.client("s3")
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read().decode("utf-8", errors="replace")
