"""`fii-ingest` CLI. Wires sub-commands to individual jobs.

Usage:
  fii-ingest seed --ticker AAPL --full
  fii-ingest prices-daily --ticker AAPL
  fii-ingest fundamentals --ticker AAPL
  fii-ingest filings --ticker AAPL
  fii-ingest macro
  fii-ingest news --ticker AAPL
  fii-ingest insiders --ticker AAPL
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import typer
from rich.console import Console
from rich.table import Table

from fii_ingest.config import get_settings
from fii_ingest.logging import configure_logging

app = typer.Typer(
    add_completion=False,
    help="FII-PRISM ingestion CLI. All commands are idempotent (upsert).",
)

console = Console()


@app.callback()
def _root(
    log_level: str = typer.Option("info", "--log-level", envvar="LOG_LEVEL"),
) -> None:
    configure_logging(log_level)


@app.command("seed")
def seed_cmd(
    ticker: str = typer.Option(..., "--ticker", "-t"),
    years_back: int = typer.Option(5, "--years-back"),
    full: bool = typer.Option(False, "--full", help="Include filings + embeddings + macro"),
    no_embeddings: bool = typer.Option(False, "--no-embeddings"),
    json_out: bool = typer.Option(False, "--json", help="Emit the report as JSON on stdout"),
) -> None:
    """Bootstrap all tables for one ticker."""
    from fii_ingest.jobs.seed import seed_ticker

    settings = get_settings()
    report = asyncio.run(
        seed_ticker(
            settings,
            symbol=ticker,
            years_back=years_back,
            include_filings=full,
            include_embeddings=not no_embeddings and full,
            include_macro=full,
        )
    )
    if json_out:
        console.print_json(json.dumps(report.summary()))
    else:
        _render_seed_table(report.summary())


@app.command("prices-daily")
def prices_daily_cmd(
    ticker: str = typer.Option(..., "--ticker", "-t"),
    years_back: int = typer.Option(5, "--years-back"),
) -> None:
    from fii_data_clients import PolygonClient
    from fii_db import get_engine, get_session_factory
    from fii_db.session import session_scope

    from fii_ingest.jobs.prices import ingest_daily

    settings = get_settings()
    if not settings.polygon_api_key:
        raise typer.BadParameter("POLYGON_API_KEY not set")

    async def _run() -> int:
        engine = get_engine(settings.database_url or "")
        factory = get_session_factory(engine)
        start = date.today() - timedelta(days=365 * years_back)
        async with PolygonClient(api_key=settings.polygon_api_key) as pg:
            with session_scope(factory) as s:
                return await ingest_daily(s, polygon=pg, symbol=ticker, start=start)

    console.print(f"[cyan]Ingested {asyncio.run(_run())} daily bars for {ticker}[/cyan]")


@app.command("fundamentals")
def fundamentals_cmd(ticker: str = typer.Option(..., "--ticker", "-t")) -> None:
    from fii_data_clients import FMPClient
    from fii_db import get_engine, get_session_factory
    from fii_db.session import session_scope

    from fii_ingest.jobs.fundamentals import ingest_fundamentals

    settings = get_settings()
    if not settings.fmp_api_key:
        raise typer.BadParameter("FMP_API_KEY not set")

    async def _run() -> int:
        engine = get_engine(settings.database_url or "")
        factory = get_session_factory(engine)
        async with FMPClient(api_key=settings.fmp_api_key) as fmp:
            with session_scope(factory) as s:
                return await ingest_fundamentals(s, fmp=fmp, symbol=ticker)

    console.print(f"[cyan]Ingested {asyncio.run(_run())} fundamental rows for {ticker}[/cyan]")


@app.command("filings")
def filings_cmd(
    ticker: str = typer.Option(..., "--ticker", "-t"),
    no_embeddings: bool = typer.Option(False, "--no-embeddings"),
) -> None:
    from fii_data_clients import EdgarClient, make_embedder
    from fii_db import get_engine, get_session_factory
    from fii_db.session import session_scope

    from fii_ingest.jobs.filings import ingest_latest_10k, ingest_latest_10q

    settings = get_settings()

    async def _run() -> dict:
        engine = get_engine(settings.database_url or "")
        factory = get_session_factory(engine)
        edgar = EdgarClient()
        embedder = make_embedder() if not no_embeddings else _NoopEmbedder()
        with session_scope(factory) as s:
            r10k = await ingest_latest_10k(
                s,
                edgar=edgar,
                embedder=embedder,
                symbol=ticker,
                raw_bucket=settings.raw_data_bucket,
            )
        with session_scope(factory) as s:
            r10q = await ingest_latest_10q(
                s,
                edgar=edgar,
                embedder=embedder,
                symbol=ticker,
                raw_bucket=settings.raw_data_bucket,
            )
        return {"10-K": r10k, "10-Q": r10q}

    result = asyncio.run(_run())
    console.print_json(json.dumps(result))


@app.command("macro")
def macro_cmd() -> None:
    from fii_data_clients import FredClient
    from fii_db import get_engine, get_session_factory
    from fii_db.session import session_scope

    from fii_ingest.jobs.macro import ingest_all_default

    settings = get_settings()
    if not settings.fred_api_key:
        raise typer.BadParameter("FRED_API_KEY not set")

    async def _run() -> int:
        engine = get_engine(settings.database_url or "")
        factory = get_session_factory(engine)
        async with FredClient(api_key=settings.fred_api_key) as fred:
            with session_scope(factory) as s:
                return await ingest_all_default(s, fred=fred)

    console.print(f"[cyan]Ingested {asyncio.run(_run())} macro observations[/cyan]")


@app.command("news")
def news_cmd(
    ticker: str = typer.Option(..., "--ticker", "-t"),
    days_back: int = typer.Option(30, "--days-back"),
) -> None:
    from fii_data_clients import FinnhubClient
    from fii_db import get_engine, get_session_factory
    from fii_db.session import session_scope

    from fii_ingest.jobs.news import ingest_company_news

    settings = get_settings()
    if not settings.finnhub_api_key:
        raise typer.BadParameter("FINNHUB_API_KEY not set")

    async def _run() -> int:
        engine = get_engine(settings.database_url or "")
        factory = get_session_factory(engine)
        async with FinnhubClient(api_key=settings.finnhub_api_key) as fh:
            with session_scope(factory) as s:
                return await ingest_company_news(s, finnhub=fh, symbol=ticker, days_back=days_back)

    console.print(f"[cyan]Ingested {asyncio.run(_run())} news items for {ticker}[/cyan]")


@app.command("insiders")
def insiders_cmd(
    ticker: str = typer.Option(..., "--ticker", "-t"),
    days_back: int = typer.Option(365, "--days-back"),
) -> None:
    from fii_data_clients import EdgarClient
    from fii_db import get_engine, get_session_factory
    from fii_db.session import session_scope

    from fii_ingest.jobs.insiders import ingest_insiders

    settings = get_settings()

    async def _run() -> int:
        engine = get_engine(settings.database_url or "")
        factory = get_session_factory(engine)
        edgar = EdgarClient()
        with session_scope(factory) as s:
            return await ingest_insiders(s, edgar=edgar, symbol=ticker, days_back=days_back)

    console.print(f"[cyan]Ingested {asyncio.run(_run())} insider transactions for {ticker}[/cyan]")


# --- Helpers ------------------------------------------------------------------------------


def _render_seed_table(summary: dict) -> None:
    t = Table(title=f"Seed report — {summary['symbol']}")
    t.add_column("Metric", style="cyan")
    t.add_column("Count", style="bold", justify="right")
    for k in (
        "daily_prices",
        "intraday_prices",
        "fundamentals_rows",
        "filings_10k",
        "filings_10q",
        "filing_chunks",
        "insiders",
        "news",
        "macro_rows",
    ):
        t.add_row(k, str(summary[k]))
    console.print(t)
    if summary["errors"]:
        console.print("[yellow]Warnings / errors:[/yellow]")
        for err in summary["errors"]:
            console.print(f"  • {err}")


class _NoopEmbedder:
    model = "noop"

    async def embed(self, texts, *, input_type: str = "document"):
        from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult

        return EmbeddingResult(
            vectors=[[0.0] * EMBEDDING_DIM for _ in texts],
            model=self.model,
        )


if __name__ == "__main__":
    app()
