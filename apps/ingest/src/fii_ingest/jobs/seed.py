"""Orchestrates a full ticker seed: prices, fundamentals, filings, insiders, news, macro."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TypeVar

import structlog
from fii_data_clients import (
    EdgarClient,
    Embedder,
    FinnhubClient,
    FMPClient,
    FredClient,
    PolygonClient,
    make_embedder,
)
from fii_db import get_engine, get_session_factory
from fii_db.session import session_scope

from fii_ingest.config import Settings
from fii_ingest.jobs.filings import ingest_latest_10k, ingest_latest_10q
from fii_ingest.jobs.fundamentals import ingest_fundamentals
from fii_ingest.jobs.insiders import ingest_insiders
from fii_ingest.jobs.macro import ingest_all_default
from fii_ingest.jobs.news import ingest_company_news
from fii_ingest.jobs.prices import ingest_daily, ingest_intraday
from fii_ingest.jobs.tickers import upsert_from_fmp_profile

log = structlog.get_logger(__name__)


@dataclass
class SeedReport:
    symbol: str
    daily_prices: int = 0
    intraday_prices: int = 0
    fundamentals_rows: int = 0
    filings_10k: int = 0
    filings_10q: int = 0
    filing_chunks: int = 0
    insiders: int = 0
    news: int = 0
    macro_rows: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "daily_prices": self.daily_prices,
            "intraday_prices": self.intraday_prices,
            "fundamentals_rows": self.fundamentals_rows,
            "filings_10k": self.filings_10k,
            "filings_10q": self.filings_10q,
            "filing_chunks": self.filing_chunks,
            "insiders": self.insiders,
            "news": self.news,
            "macro_rows": self.macro_rows,
            "errors": self.errors,
        }


T = TypeVar("T")


async def _step(
    *, symbol: str, name: str, fn: Callable[[], Awaitable[T]]
) -> tuple[T | None, Exception | None]:
    """Run one seed step and emit start/complete log lines around it.

    Catches any exception so the caller can record it on the report and move
    on — keeps the existing per-step error-isolation behavior. Returns the
    tuple ``(result, exc)`` so callers can branch without re-raising.
    """
    start = time.perf_counter()
    log.info("seed_step_start", symbol=symbol, step=name)
    try:
        result = await fn()
    except Exception as exc:
        log.exception(
            "seed_step_failed",
            symbol=symbol,
            step=name,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return None, exc
    log.info(
        "seed_step_complete",
        symbol=symbol,
        step=name,
        duration_ms=int((time.perf_counter() - start) * 1000),
    )
    return result, None


def _step_skipped(*, symbol: str, name: str, reason: str) -> None:
    """Surface a step that's being deliberately skipped. The previous
    embedder-gated 10-K/10-Q skip emitted nothing and looked identical to a
    successful zero-rows run; that's how the bug was hiding."""
    log.warning("seed_step_skipped", symbol=symbol, step=name, reason=reason)


async def seed_ticker(
    settings: Settings,
    *,
    symbol: str,
    years_back: int = 5,
    include_macro: bool = True,
    include_filings: bool = True,
    include_embeddings: bool = True,
) -> SeedReport:
    """End-to-end ticker bootstrap. Each step is fail-isolated so one bad provider
    doesn't kill the rest."""
    symbol = symbol.upper()
    report = SeedReport(symbol=symbol)

    engine = get_engine(settings.database_url or "")
    factory = get_session_factory(engine)

    start = date.today() - timedelta(days=365 * years_back)
    end = date.today()

    # --- FMP profile + ticker upsert -----------------------------------------------------
    if settings.fmp_api_key:
        try:
            async with FMPClient(api_key=settings.fmp_api_key) as fmp:
                profile = await fmp.get_profile(symbol)
                with session_scope(factory) as s:
                    upsert_from_fmp_profile(s, symbol, profile)
                # Fundamentals from the same client session.
                async with FMPClient(api_key=settings.fmp_api_key) as fmp2:
                    with session_scope(factory) as s:
                        report.fundamentals_rows = await ingest_fundamentals(
                            s, fmp=fmp2, symbol=symbol
                        )
        except Exception as exc:
            report.errors.append(f"fmp: {exc}")
            log.exception("fmp_step_failed", symbol=symbol)
    else:
        # Minimal ticker row so downstream foreign keys work.
        with session_scope(factory) as s:
            upsert_from_fmp_profile(s, symbol, None)
        report.errors.append("fmp: FMP_API_KEY not set; ticker upserted with minimal fields")

    # --- Polygon prices ------------------------------------------------------------------
    if settings.polygon_api_key:
        try:
            async with PolygonClient(api_key=settings.polygon_api_key) as pg:
                with session_scope(factory) as s:
                    report.daily_prices = await ingest_daily(
                        s, polygon=pg, symbol=symbol, start=start, end=end
                    )
                with session_scope(factory) as s:
                    report.intraday_prices = await ingest_intraday(s, polygon=pg, symbol=symbol)
        except Exception as exc:
            report.errors.append(f"polygon: {exc}")
            log.exception("polygon_step_failed", symbol=symbol)
    else:
        report.errors.append("polygon: POLYGON_API_KEY not set; skipping prices")

    # --- Edgar filings + chunks ----------------------------------------------------------
    if include_filings:
        edgar = EdgarClient()
        try:
            embedder: Embedder | None = make_embedder() if include_embeddings else None
        except Exception as exc:
            report.errors.append(f"embedder: {exc}")
            log.exception("embedder_init_failed")
            embedder = None

        if not embedder:
            # Bug 2 root cause: this branch used to silently skip 10-K/10-Q
            # ingest with no log line — the seed report showed filings_10k=0 /
            # filings_10q=0 and there was no way to tell whether the path ran
            # or got gated out. Now we surface it explicitly.
            reason = (
                "include_embeddings=False"
                if not include_embeddings
                else "embedder_init_failed (see embedder_init_failed log above)"
            )
            _step_skipped(symbol=symbol, name="10k", reason=reason)
            _step_skipped(symbol=symbol, name="10q", reason=reason)
            report.errors.append(
                f"filings: skipped — {reason}. Set VOYAGE_API_KEY or fix Bedrock to enable."
            )
        else:
            async def _do_10k():
                with session_scope(factory) as s:
                    return await ingest_latest_10k(
                        s,
                        edgar=edgar,
                        embedder=embedder,
                        symbol=symbol,
                        raw_bucket=settings.raw_data_bucket,
                    )

            r10k, exc10k = await _step(symbol=symbol, name="10k", fn=_do_10k)
            if exc10k is not None:
                report.errors.append(f"edgar_filings_10k: {exc10k}")
            elif r10k is not None:
                report.filings_10k = 1 if r10k["filing_id"] else 0
                report.filing_chunks += int(r10k["chunks"])

            async def _do_10q():
                with session_scope(factory) as s:
                    return await ingest_latest_10q(
                        s,
                        edgar=edgar,
                        embedder=embedder,
                        symbol=symbol,
                        raw_bucket=settings.raw_data_bucket,
                    )

            r10q, exc10q = await _step(symbol=symbol, name="10q", fn=_do_10q)
            if exc10q is not None:
                report.errors.append(f"edgar_filings_10q: {exc10q}")
            elif r10q is not None:
                report.filings_10q = 1 if r10q["filing_id"] else 0
                report.filing_chunks += int(r10q["chunks"])

    # --- Insider transactions ------------------------------------------------------------
    try:
        edgar = EdgarClient()
        with session_scope(factory) as s:
            report.insiders = await ingest_insiders(s, edgar=edgar, symbol=symbol)
    except Exception as exc:
        report.errors.append(f"insiders: {exc}")
        log.exception("insiders_step_failed", symbol=symbol)

    # --- Finnhub news --------------------------------------------------------------------
    if settings.finnhub_api_key:
        try:
            async with FinnhubClient(api_key=settings.finnhub_api_key) as fh:
                with session_scope(factory) as s:
                    report.news = await ingest_company_news(s, finnhub=fh, symbol=symbol)
        except Exception as exc:
            report.errors.append(f"finnhub: {exc}")
            log.exception("finnhub_step_failed", symbol=symbol)
    else:
        report.errors.append("finnhub: FINNHUB_API_KEY not set; skipping news")

    # --- FRED macro ---------------------------------------------------------------------
    if include_macro:
        if settings.fred_api_key:
            try:
                async with FredClient(api_key=settings.fred_api_key) as fred:
                    macro_report = await ingest_all_default(factory, fred=fred)
                report.macro_rows = macro_report.total_rows
                for series_id, err in macro_report.failed:
                    report.errors.append(f"fred:{series_id}: {err}")
            except Exception as exc:
                # Only the orchestrator-level path (e.g., FredClient setup) reaches
                # here now — per-series failures are handled inside ingest_all_default.
                report.errors.append(f"fred: {exc}")
                log.exception("fred_step_failed")
        else:
            report.errors.append("fred: FRED_API_KEY not set; skipping macro")

    await asyncio.sleep(0)  # yield once to flush pending log events
    log.info("seed_complete", **report.summary())
    return report
