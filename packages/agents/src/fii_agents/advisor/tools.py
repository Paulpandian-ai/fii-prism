"""Advisor tool implementations + Anthropic tool schemas.

Every tool returns a JSON-serializable dict; the loop wraps each into a tool_result
content block. Tools are deterministic except for run_quick_analysis (which kicks
off a quick_refresh and waits for it to finish). Failure modes return structured
{"error": "..."} dicts so the model can explain to the user instead of crashing.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from fii_db import (
    Analysis,
    AnalysisSpecialistOutput,
    Position,
    PriceDaily,
    Ticker,
    UserSettings,
    Watchlist,
)
from fii_db.session import session_scope
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

log = structlog.get_logger(__name__)


# --- Anthropic tool schemas ---------------------------------------------------------------

ADVISOR_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_my_portfolio",
        "description": (
            "Return the user's current open positions: symbol, shares, cost basis "
            "per share, opened_at, current price, market value, and unrealized P/L. "
            "Use this whenever the user asks about 'my position', 'my holdings', or "
            "'should I buy/hold/sell X'."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_my_watchlist",
        "description": "Return the user's watchlist tickers (symbols + when added).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_latest_analysis",
        "description": (
            "Return the latest deep_dive or quick_refresh for a symbol. Includes "
            "analysis_id, recommendation, confidence, fii_score, age_days, and "
            "specialist outputs. Use age_days to decide if a fresh analysis is needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_quick_analysis",
        "description": (
            "Trigger a fresh quick_refresh analysis on a symbol and WAIT for it to "
            "complete. Returns the new analysis_id + summary. Use this when "
            "get_latest_analysis returns age_days > 7 or status=='not_found'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "event_type": {
                    "type": "string",
                    "enum": [
                        "price_shock",
                        "news_shock",
                        "8k_filed",
                        "earnings_release",
                        "macro_surprise",
                    ],
                    "description": "Optional scope hint; defaults to broadest scope.",
                },
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_peer_comparison",
        "description": (
            "Return up to 5 peer tickers in the same sector (or sector+industry) for "
            "comparison, with their latest fii_score and recommendation if available. "
            "Use for 'what are better alternatives to X' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_tax_loss_harvest",
        "description": (
            "Compute tax consequences of selling N shares of a symbol from the user's "
            "open positions: short-term vs long-term gain/loss, holding period, "
            "estimated tax at 24% short / 15% long. Caller may override rates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "shares": {"type": "number", "exclusiveMinimum": 0},
                "short_term_rate": {"type": "number", "minimum": 0, "maximum": 1},
                "long_term_rate": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["symbol", "shares"],
            "additionalProperties": False,
        },
    },
    {
        "name": "calculate_portfolio_impact",
        "description": (
            "Simulate hypothetical trades against the current portfolio. Returns "
            "before/after concentration per position, cash balance, sector mix, and "
            "any concentration_breach flags vs the user's target_concentration_pct."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trades": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "side": {"type": "string", "enum": ["buy", "sell"]},
                            "shares": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["symbol", "side", "shares"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                }
            },
            "required": ["trades"],
            "additionalProperties": False,
        },
    },
]


# --- Context + dispatch -------------------------------------------------------------------


# Forward type for run_quick_analysis. The advisor module supplies this so tools.py
# stays free of orchestrator imports (avoids a cycle: orchestrator → advisor → tools → orchestrator).
RunQuickFn = Callable[[str, str | None], Awaitable[dict[str, Any]]]


@dataclass
class AdvisorToolContext:
    factory: sessionmaker
    user_id: str
    run_quick_analysis: RunQuickFn | None = None


async def dispatch(name: str, args: dict[str, Any], ctx: AdvisorToolContext) -> dict[str, Any]:
    try:
        if name == "get_my_portfolio":
            return _get_my_portfolio(ctx)
        if name == "get_my_watchlist":
            return _get_my_watchlist(ctx)
        if name == "get_latest_analysis":
            return _get_latest_analysis(ctx, symbol=str(args["symbol"]))
        if name == "run_quick_analysis":
            if ctx.run_quick_analysis is None:
                return {"error": "run_quick_analysis not wired in this context"}
            return await ctx.run_quick_analysis(
                str(args["symbol"]).upper(),
                str(args["event_type"]) if args.get("event_type") else None,
            )
        if name == "get_peer_comparison":
            return _get_peer_comparison(
                ctx, symbol=str(args["symbol"]), limit=int(args.get("limit", 5))
            )
        if name == "calculate_tax_loss_harvest":
            return _calculate_tax_loss_harvest(
                ctx,
                symbol=str(args["symbol"]),
                shares=float(args["shares"]),
                short_term_rate=float(args.get("short_term_rate", 0.24)),
                long_term_rate=float(args.get("long_term_rate", 0.15)),
            )
        if name == "calculate_portfolio_impact":
            return _calculate_portfolio_impact(ctx, trades=list(args["trades"]))
        return {"error": f"unknown tool: {name}"}
    except Exception as exc:
        log.exception("advisor_tool_error", tool=name)
        return {"error": str(exc)}


# --- Tool implementations -----------------------------------------------------------------


def _latest_close(s, symbol: str) -> float | None:
    row = s.execute(
        select(PriceDaily.close)
        .where(PriceDaily.symbol == symbol.upper())
        .order_by(desc(PriceDaily.trade_date))
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return float(row)


def _get_my_portfolio(ctx: AdvisorToolContext) -> dict[str, Any]:
    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(Position)
                .where(Position.user_id == ctx.user_id, Position.closed_at.is_(None))
                .order_by(Position.opened_at.asc())
            )
            .scalars()
            .all()
        )
        cash_row = s.execute(
            select(UserSettings.cash_balance_usd).where(UserSettings.user_id == ctx.user_id)
        ).scalar_one_or_none()
        cash = float(cash_row) if cash_row is not None else 0.0

        positions: list[dict[str, Any]] = []
        total_value = cash
        for r in rows:
            shares = float(r.shares)
            cost_basis = float(r.cost_basis)
            price = _latest_close(s, r.symbol)
            mv = shares * price if price is not None else None
            unrealized = (price - cost_basis) * shares if price is not None else None
            unrealized_pct = (
                (price / cost_basis - 1.0) if (price is not None and cost_basis > 0) else None
            )
            held_days = (datetime.now(UTC).date() - r.opened_at.date()).days
            if mv is not None:
                total_value += mv
            positions.append(
                {
                    "position_id": str(r.id),
                    "symbol": r.symbol,
                    "shares": shares,
                    "cost_basis_per_share": cost_basis,
                    "opened_at": r.opened_at.date().isoformat(),
                    "held_days": held_days,
                    "long_term_eligible": held_days >= 365,
                    "current_price": price,
                    "market_value_usd": mv,
                    "unrealized_pl_usd": unrealized,
                    "unrealized_pct": unrealized_pct,
                    "notes": r.notes,
                }
            )

        # Concentration after we know the total.
        for p in positions:
            mv = p["market_value_usd"]
            p["weight_pct"] = (mv / total_value * 100.0) if (mv and total_value > 0) else None

    return {
        "positions": positions,
        "cash_balance_usd": cash,
        "total_value_usd": total_value,
        "as_of": datetime.now(UTC).isoformat(),
    }


def _get_my_watchlist(ctx: AdvisorToolContext) -> dict[str, Any]:
    with session_scope(ctx.factory) as s:
        rows = (
            s.execute(
                select(Watchlist)
                .where(Watchlist.user_id == ctx.user_id)
                .order_by(Watchlist.added_at.desc())
            )
            .scalars()
            .all()
        )
        return {
            "watchlist": [
                {"symbol": r.symbol, "added_at": r.added_at.isoformat(), "notes": r.notes}
                for r in rows
            ]
        }


def _get_latest_analysis(ctx: AdvisorToolContext, *, symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    with session_scope(ctx.factory) as s:
        row = s.execute(
            select(Analysis)
            .where(Analysis.symbol == symbol, Analysis.status == "succeeded")
            .order_by(desc(Analysis.initiated_at))
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return {
                "status": "not_found",
                "symbol": symbol,
                "note": "No prior analysis. Recommend running a deep_dive before answering.",
            }
        specialist_rows = (
            s.execute(
                select(AnalysisSpecialistOutput).where(
                    AnalysisSpecialistOutput.analysis_id == row.analysis_id
                )
            )
            .scalars()
            .all()
        )
        specialists = {r.specialist_name: r.output_json for r in specialist_rows}
        age_days = (datetime.now(UTC) - row.initiated_at).days
        return {
            "status": "found",
            "analysis_id": str(row.analysis_id),
            "symbol": row.symbol,
            "analysis_type": row.analysis_type,
            "initiated_at": row.initiated_at.isoformat(),
            "age_days": age_days,
            "is_stale": age_days > 7,
            "recommendation": row.recommendation,
            "confidence": row.confidence,
            "fii_score": float(row.fii_score) if row.fii_score is not None else None,
            "orchestrator_summary": row.orchestrator_summary,
            "specialists": specialists,
        }


def _get_peer_comparison(ctx: AdvisorToolContext, *, symbol: str, limit: int) -> dict[str, Any]:
    symbol = symbol.upper()
    limit = max(1, min(10, limit))
    with session_scope(ctx.factory) as s:
        target = s.execute(select(Ticker).where(Ticker.symbol == symbol)).scalar_one_or_none()
        if target is None or not target.sector:
            return {
                "status": "no_peers",
                "symbol": symbol,
                "note": (
                    "Sector classification missing for this symbol. Run "
                    "`fii-ingest seed -t <SYMBOL>` to populate."
                ),
            }
        peers = (
            s.execute(
                select(Ticker)
                .where(
                    Ticker.symbol != symbol,
                    Ticker.is_active.is_(True),
                    Ticker.sector == target.sector,
                )
                .limit(limit * 2)
            )
            .scalars()
            .all()
        )
        peer_rows: list[dict[str, Any]] = []
        for p in peers[:limit]:
            latest = s.execute(
                select(Analysis)
                .where(Analysis.symbol == p.symbol, Analysis.status == "succeeded")
                .order_by(desc(Analysis.initiated_at))
                .limit(1)
            ).scalar_one_or_none()
            peer_rows.append(
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "industry": p.industry,
                    "market_cap_bucket": p.market_cap_bucket,
                    "latest_analysis_id": str(latest.analysis_id) if latest else None,
                    "fii_score": (
                        float(latest.fii_score) if latest and latest.fii_score is not None else None
                    ),
                    "recommendation": latest.recommendation if latest else None,
                }
            )
        return {
            "status": "ok",
            "target_sector": target.sector,
            "target_industry": target.industry,
            "peers": peer_rows,
        }


def _calculate_tax_loss_harvest(
    ctx: AdvisorToolContext,
    *,
    symbol: str,
    shares: float,
    short_term_rate: float,
    long_term_rate: float,
) -> dict[str, Any]:
    symbol = symbol.upper()
    with session_scope(ctx.factory) as s:
        positions = (
            s.execute(
                select(Position)
                .where(
                    Position.user_id == ctx.user_id,
                    Position.symbol == symbol,
                    Position.closed_at.is_(None),
                )
                .order_by(Position.opened_at.asc())
            )
            .scalars()
            .all()
        )
        if not positions:
            return {
                "status": "no_position",
                "symbol": symbol,
                "note": f"No open position in {symbol}.",
            }
        price = _latest_close(s, symbol)
        if price is None:
            return {
                "status": "no_price",
                "symbol": symbol,
                "note": "Latest close missing; run `fii-ingest prices-daily` for this ticker.",
            }

        # FIFO lot consumption.
        remaining = shares
        lots: list[dict[str, Any]] = []
        today = datetime.now(UTC).date()
        for p in positions:
            if remaining <= 0:
                break
            avail = float(p.shares)
            take = min(avail, remaining)
            held_days = (today - p.opened_at.date()).days
            cb = float(p.cost_basis)
            gain_per_share = price - cb
            gain = gain_per_share * take
            term = "long" if held_days >= 365 else "short"
            lots.append(
                {
                    "position_id": str(p.id),
                    "shares": take,
                    "opened_at": p.opened_at.date().isoformat(),
                    "held_days": held_days,
                    "term": term,
                    "cost_basis_per_share": cb,
                    "proceeds_per_share": price,
                    "realized_gain_usd": gain,
                }
            )
            remaining -= take

        if remaining > 0:
            return {
                "status": "insufficient_shares",
                "symbol": symbol,
                "requested_shares": shares,
                "available_shares": shares - remaining,
            }

        st_gain = sum(lot["realized_gain_usd"] for lot in lots if lot["term"] == "short")
        lt_gain = sum(lot["realized_gain_usd"] for lot in lots if lot["term"] == "long")
        st_tax = max(0.0, st_gain) * short_term_rate
        lt_tax = max(0.0, lt_gain) * long_term_rate

    return {
        "status": "ok",
        "symbol": symbol,
        "shares_sold": shares,
        "current_price": price,
        "lots": lots,
        "short_term_gain_usd": st_gain,
        "long_term_gain_usd": lt_gain,
        "estimated_tax_usd": st_tax + lt_tax,
        "rates_used": {"short_term": short_term_rate, "long_term": long_term_rate},
        "note": "Estimate only; consult a tax professional. Wash-sale rule not modeled.",
    }


def _calculate_portfolio_impact(
    ctx: AdvisorToolContext, *, trades: list[dict[str, Any]]
) -> dict[str, Any]:
    with session_scope(ctx.factory) as s:
        positions = (
            s.execute(
                select(Position).where(
                    Position.user_id == ctx.user_id, Position.closed_at.is_(None)
                )
            )
            .scalars()
            .all()
        )
        cash_row = s.execute(
            select(UserSettings.cash_balance_usd).where(UserSettings.user_id == ctx.user_id)
        ).scalar_one_or_none()
        target_row = s.execute(
            select(UserSettings.target_concentration_pct).where(UserSettings.user_id == ctx.user_id)
        ).scalar_one_or_none()
        target_concentration = float(target_row) if target_row is not None else 10.0

        # Build an in-memory book.
        book: dict[str, dict[str, Any]] = {}
        for p in positions:
            shares = float(p.shares)
            cb = float(p.cost_basis)
            book.setdefault(p.symbol, {"shares": 0.0, "cost_basis_total": 0.0, "sector": None})
            book[p.symbol]["shares"] += shares
            book[p.symbol]["cost_basis_total"] += shares * cb
        cash_before = float(cash_row) if cash_row is not None else 0.0
        cash_after = cash_before

        # Sector lookup.
        sectors = dict(
            s.execute(select(Ticker.symbol, Ticker.sector).where(Ticker.is_active.is_(True))).all()
        )

        # Apply trades.
        warnings: list[str] = []
        for t in trades:
            sym = str(t["symbol"]).upper()
            side = str(t["side"]).lower()
            n = float(t["shares"])
            price = _latest_close(s, sym)
            if price is None:
                return {
                    "status": "no_price",
                    "symbol": sym,
                    "note": "Cannot simulate — missing latest close.",
                }
            sector = sectors.get(sym)
            book.setdefault(sym, {"shares": 0.0, "cost_basis_total": 0.0, "sector": sector})
            book[sym]["sector"] = sector
            if side == "buy":
                cost = n * price
                cash_after -= cost
                book[sym]["shares"] += n
                book[sym]["cost_basis_total"] += cost
                if cash_after < 0:
                    warnings.append(f"Trade exceeds cash by ${-cash_after:,.2f}")
            else:  # sell
                if n > book[sym]["shares"] + 1e-9:
                    return {
                        "status": "insufficient_shares",
                        "symbol": sym,
                        "have": book[sym]["shares"],
                        "want": n,
                    }
                # FIFO-ish proceeds; cost basis reduced proportionally.
                share_frac = n / book[sym]["shares"] if book[sym]["shares"] > 0 else 0.0
                book[sym]["cost_basis_total"] *= 1.0 - share_frac
                book[sym]["shares"] -= n
                cash_after += n * price

        # Build the after snapshot.
        positions_after: list[dict[str, Any]] = []
        total_after = cash_after
        for sym, b in book.items():
            if b["shares"] <= 0:
                continue
            price = _latest_close(s, sym)
            if price is None:
                continue
            mv = b["shares"] * price
            total_after += mv
            positions_after.append(
                {
                    "symbol": sym,
                    "sector": b["sector"] or sectors.get(sym),
                    "shares": b["shares"],
                    "market_value_usd": mv,
                }
            )
        sector_mix: dict[str, float] = {}
        breaches: list[dict[str, Any]] = []
        for p in positions_after:
            weight = p["market_value_usd"] / total_after * 100.0 if total_after > 0 else 0.0
            p["weight_pct"] = weight
            sec = p["sector"] or "Unknown"
            sector_mix[sec] = sector_mix.get(sec, 0.0) + weight
            if weight > target_concentration:
                breaches.append(
                    {
                        "symbol": p["symbol"],
                        "weight_pct": weight,
                        "target_pct": target_concentration,
                    }
                )

    return {
        "status": "ok",
        "cash_before_usd": cash_before,
        "cash_after_usd": cash_after,
        "total_value_after_usd": total_after,
        "positions_after": positions_after,
        "sector_mix_pct": sector_mix,
        "concentration_breaches": breaches,
        "target_concentration_pct": target_concentration,
        "warnings": warnings,
        "note": "Simulation only; ignores commissions, slippage, taxes.",
    }


# --- Helpers ------------------------------------------------------------------------------


def ensure_user_settings_row(factory: sessionmaker, user_id: str) -> None:
    """Idempotent — guarantees a UserSettings row exists for the given user."""
    with session_scope(factory) as s:
        s.execute(
            pg_insert(UserSettings)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=[UserSettings.user_id])
        )


def referenced_analysis_ids_from_tool_calls(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Pull analysis_ids out of tool_result payloads so the API can store them on the
    chat_messages row (for the 'sources' UI)."""
    ids: list[str] = []
    for tc in tool_calls:
        result = tc.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("analysis_id"):
            ids.append(str(result["analysis_id"]))
        for p in result.get("peers", []) or []:
            if isinstance(p, dict) and p.get("latest_analysis_id"):
                ids.append(str(p["latest_analysis_id"]))
    # De-dupe, keep order, and validate as UUIDs.
    out: list[str] = []
    seen: set[str] = set()
    for i in ids:
        try:
            uuid.UUID(i)
        except (ValueError, TypeError):
            continue
        if i not in seen:
            out.append(i)
            seen.add(i)
    return out


# Suppress unused-import lint for typing-only helpers used in docstrings.
_unused = (date, timedelta, math, Decimal)
