"""News ingestion from Finnhub. Sentiment is deliberately NOT populated here —
the News agent scores sentiment lazily when an analysis runs."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from fii_data_clients import FinnhubClient
from fii_db import NewsItem
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from fii_ingest.bulk import chunked_upsert

log = structlog.get_logger(__name__)


def _content_hash(source: str | None, url: str | None, headline: str) -> str:
    h = hashlib.sha256()
    h.update((source or "").encode("utf-8"))
    h.update(b"\x1f")
    h.update((url or "").encode("utf-8"))
    h.update(b"\x1f")
    h.update(headline.encode("utf-8"))
    return h.hexdigest()


async def ingest_company_news(
    session: Session,
    *,
    finnhub: FinnhubClient,
    symbol: str,
    days_back: int = 30,
) -> int:
    since = date.today() - timedelta(days=days_back)
    items = await finnhub.get_company_news(symbol, since=since)
    if not items:
        return 0

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        headline = str(it.get("headline") or "").strip()
        if not headline:
            continue
        url = it.get("url")
        source = it.get("source")
        ch = _content_hash(source, url, headline)
        if ch in seen:
            continue
        seen.add(ch)
        rows.append(
            {
                "content_hash": ch,
                "symbols": [symbol.upper()],
                "headline": headline,
                "summary": it.get("summary") or None,
                "url": url,
                "source": source,
                "published_at": _published_at(it.get("datetime")),
            }
        )
    if not rows:
        return 0

    def _build(chunk: list[dict[str, Any]]):
        stmt = pg_insert(NewsItem).values(chunk)
        return stmt.on_conflict_do_nothing(index_elements=[NewsItem.content_hash])

    chunked_upsert(session, rows, build_stmt=_build, label=f"news_items:{symbol}")
    log.info("news_upserted", symbol=symbol, rows=len(rows))
    return len(rows)


def _published_at(ts: Any) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC)
    except (ValueError, TypeError):
        return None
