"""Filing ingestion: pull the latest 10-K/10-Q via edgartools, upload raw text to S3,
store metadata + chunk text in Postgres in one transaction, then embed in a SECOND
transaction. The split matters because the embedder (Voyage / Bedrock) is the slowest +
most failure-prone step — a Voyage rate-limit used to roll back the entire filing
record we'd just paid EDGAR to fetch. Now an embed failure leaves the filing + chunk
rows on disk with `embedding_status='pending'`, ready for the
``fii-ingest backfill-embeddings`` command to retry.
"""

from __future__ import annotations

import uuid
from typing import Any

import boto3
import structlog
from fii_data_clients import EdgarClient, Embedder, FilingRecord
from fii_db import Filing, FilingChunk, FormType
from fii_db.session import session_scope
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from fii_ingest.bulk import chunked_upsert
from fii_ingest.chunking import Chunk, chunk_filing

log = structlog.get_logger(__name__)


def _s3_key(symbol: str, accession_no: str, form_type: str) -> str:
    return f"filings/{symbol.upper()}/{form_type}/{accession_no}.txt"


def _upload_text(bucket: str | None, key: str, text: str) -> str | None:
    if not bucket:
        return None
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket, Key=key, Body=text.encode("utf-8"), ContentType="text/plain; charset=utf-8"
    )
    return key


async def ingest_latest_10k(
    factory: sessionmaker,
    *,
    edgar: EdgarClient,
    embedder: Embedder,
    symbol: str,
    raw_bucket: str | None,
) -> dict[str, Any]:
    record = await edgar.get_latest_10k(symbol)
    if record is None:
        log.info(
            "filings_summary",
            symbol=symbol,
            form="10-K",
            raw=0,
            persisted=0,
            chunks=0,
            note="no_10k_found",
        )
        return {"filing_id": None, "chunks": 0, "embedded": 0}
    result = await _store_filing(factory, edgar, embedder, symbol, record, raw_bucket)
    log.info(
        "filings_summary",
        symbol=symbol,
        form="10-K",
        raw=1,
        persisted=1 if result["filing_id"] else 0,
        chunks=int(result["chunks"]),
        embedded=int(result.get("embedded", 0)),
    )
    return result


async def ingest_latest_10q(
    factory: sessionmaker,
    *,
    edgar: EdgarClient,
    embedder: Embedder,
    symbol: str,
    raw_bucket: str | None,
) -> dict[str, Any]:
    record = await edgar.get_latest_10q(symbol)
    if record is None:
        log.info(
            "filings_summary",
            symbol=symbol,
            form="10-Q",
            raw=0,
            persisted=0,
            chunks=0,
            note="no_10q_found",
        )
        return {"filing_id": None, "chunks": 0, "embedded": 0}
    result = await _store_filing(factory, edgar, embedder, symbol, record, raw_bucket)
    log.info(
        "filings_summary",
        symbol=symbol,
        form="10-Q",
        raw=1,
        persisted=1 if result["filing_id"] else 0,
        chunks=int(result["chunks"]),
        embedded=int(result.get("embedded", 0)),
    )
    return result


async def _store_filing(
    factory: sessionmaker,
    edgar: EdgarClient,
    embedder: Embedder,
    symbol: str,
    record: FilingRecord,
    raw_bucket: str | None,
) -> dict[str, Any]:
    """Two-phase persistence:
        Phase A — Filing row + chunk text rows (embedding NULL, status='pending')
                  in one transaction. Always commits if the DB accepts the rows.
        Phase B — embed + UPDATE chunks SET embedding=..., status='ok' in a second
                  transaction. On any exception, log and leave rows pending. The
                  backfill command picks them up later.

    The signature takes ``factory: sessionmaker`` (not a session) so we own the
    transaction boundaries — same pattern as synthesis_runner.synthesize_from_cache.
    """
    s3_key: str | None = None
    if record.raw_text and raw_bucket:
        s3_key = _s3_key(symbol, record.accession_no, record.form_type)
        _upload_text(raw_bucket, s3_key, record.raw_text)

    form_type = _normalize_form_type(record.form_type)
    chunks = chunk_filing(sections=record.sections, full_text=record.raw_text)

    # --- Phase A: persist the filing + chunk text (no embedding yet) -----------------
    with session_scope(factory) as s:
        filing_id = str(uuid.uuid4())
        stmt = pg_insert(Filing).values(
            filing_id=filing_id,
            symbol=symbol.upper(),
            form_type=form_type.value,
            filed_at=record.filed_at,
            period_of_report=record.period_of_report,
            accession_no=record.accession_no,
            url=record.url,
            raw_text_s3_key=s3_key,
            title=record.title,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["accession_no"],
            set_={
                "raw_text_s3_key": stmt.excluded.raw_text_s3_key,
                "url": stmt.excluded.url,
                "title": stmt.excluded.title,
            },
        ).returning(Filing.filing_id)
        filing_id = str(s.execute(stmt).scalar_one())

        if chunks:
            # Delete-then-insert: simplest way to handle re-ingestion without a
            # dedupe key on chunks.
            s.execute(
                FilingChunk.__table__.delete().where(FilingChunk.filing_id == filing_id)
            )
            chunk_rows = [
                {
                    "filing_id": filing_id,
                    "symbol": symbol.upper(),
                    "section_name": c.section_name,
                    "chunk_index": c.index,
                    "chunk_text": c.text,
                    "token_count": c.token_count,
                    "embedding": None,
                    "embedding_model": None,
                    "embedding_status": "pending",
                }
                for c in chunks
            ]

            def _build(rows: list[dict[str, Any]]):
                return pg_insert(FilingChunk).values(rows)

            chunked_upsert(s, chunk_rows, build_stmt=_build, label=f"filing_chunks:{filing_id}")

    log.info(
        "filing_stored",
        symbol=symbol,
        form=record.form_type,
        accession_no=record.accession_no,
        chunks=len(chunks),
        s3_key=s3_key,
        embedding="pending",
    )

    # --- Phase B: embed in a separate transaction; failures leave rows pending -----
    embedded = 0
    if chunks:
        try:
            embedded = await _embed_chunks_for_filing(
                factory, filing_id=filing_id, symbol=symbol, chunks=chunks, embedder=embedder
            )
        except Exception as exc:
            # The brief explicitly: don't swallow, surface as a warning. Don't
            # re-raise — the filing is already persisted; the backfill command
            # will retry on the next run.
            log.warning(
                "filing_chunks_embed_failed",
                symbol=symbol,
                filing_id=filing_id,
                form=record.form_type,
                chunks=len(chunks),
                exc=str(exc),
                exc_type=type(exc).__name__,
                hint=(
                    "rows persisted with embedding_status='pending'; "
                    "run `fii-ingest backfill-embeddings --ticker " + symbol.upper() + "`"
                ),
            )

    return {"filing_id": filing_id, "chunks": len(chunks), "embedded": embedded}


async def _embed_chunks_for_filing(
    factory: sessionmaker,
    *,
    filing_id: str,
    symbol: str,
    chunks: list[Chunk],
    embedder: Embedder,
) -> int:
    """Embed a filing's chunks in batches and write the vectors back. Runs in
    its own session_scope so a partial-batch success commits."""
    BATCH = 64
    total_embedded = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i : i + BATCH]
        result = await embedder.embed([c.text for c in batch], input_type="document")
        # The chunks are matched to rows by (filing_id, chunk_index). UPDATE per
        # row keeps the SQL simple and lets us write per-batch — partial success
        # is fine because each batch commits via session_scope below.
        with session_scope(factory) as s:
            for c, vec in zip(batch, result.vectors, strict=True):
                s.execute(
                    sql_update(FilingChunk)
                    .where(
                        FilingChunk.filing_id == filing_id,
                        FilingChunk.chunk_index == c.index,
                    )
                    .values(
                        embedding=vec,
                        embedding_model=result.model,
                        embedding_status="ok",
                    )
                )
        total_embedded += len(batch)
        log.info(
            "filing_chunks_embedded_batch",
            filing_id=filing_id,
            symbol=symbol,
            batch_index=i // BATCH,
            batch_size=len(batch),
            tokens=result.tokens,
            cost_usd=round(result.cost_usd, 6),
        )
    return total_embedded


def _normalize_form_type(raw: str) -> FormType:
    s = raw.upper().strip()
    mapping = {
        "10-K": FormType.TEN_K,
        "10-K/A": FormType.TEN_K,
        "10-Q": FormType.TEN_Q,
        "10-Q/A": FormType.TEN_Q,
        "8-K": FormType.EIGHT_K,
        "4": FormType.FORM_4,
        "13F-HR": FormType.THIRTEEN_F,
        "13F": FormType.THIRTEEN_F,
        "DEF 14A": FormType.PROXY,
    }
    return mapping.get(s, FormType.EIGHT_K)
