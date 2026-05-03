"""Filing ingestion: pull the latest 10-K/10-Q via edgartools, upload raw text to S3,
store metadata in Postgres, chunk + embed, and insert chunk rows.
"""

from __future__ import annotations

import uuid
from typing import Any

import boto3
import structlog
from fii_data_clients import EdgarClient, Embedder, FilingRecord
from fii_db import Filing, FilingChunk, FormType
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

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
    session: Session,
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
        return {"filing_id": None, "chunks": 0}
    result = await _store_filing(session, edgar, embedder, symbol, record, raw_bucket)
    log.info(
        "filings_summary",
        symbol=symbol,
        form="10-K",
        raw=1,
        persisted=1 if result["filing_id"] else 0,
        chunks=int(result["chunks"]),
    )
    return result


async def ingest_latest_10q(
    session: Session,
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
        return {"filing_id": None, "chunks": 0}
    result = await _store_filing(session, edgar, embedder, symbol, record, raw_bucket)
    log.info(
        "filings_summary",
        symbol=symbol,
        form="10-Q",
        raw=1,
        persisted=1 if result["filing_id"] else 0,
        chunks=int(result["chunks"]),
    )
    return result


async def _store_filing(
    session: Session,
    edgar: EdgarClient,
    embedder: Embedder,
    symbol: str,
    record: FilingRecord,
    raw_bucket: str | None,
) -> dict[str, Any]:
    s3_key: str | None = None
    if record.raw_text and raw_bucket:
        s3_key = _s3_key(symbol, record.accession_no, record.form_type)
        _upload_text(raw_bucket, s3_key, record.raw_text)

    filing_id = str(uuid.uuid4())
    form_type = _normalize_form_type(record.form_type)

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
    result = session.execute(stmt).scalar_one()
    filing_id = str(result)

    # Chunk + embed. Skip embeddings on empty text.
    chunks = chunk_filing(sections=record.sections, full_text=record.raw_text)
    chunk_count = 0
    if chunks:
        chunk_count = await _insert_chunks(session, filing_id, symbol, chunks, embedder)

    log.info(
        "filing_stored",
        symbol=symbol,
        form=record.form_type,
        accession_no=record.accession_no,
        chunks=chunk_count,
        s3_key=s3_key,
    )
    return {"filing_id": filing_id, "chunks": chunk_count}


async def _insert_chunks(
    session: Session, filing_id: str, symbol: str, chunks: list[Chunk], embedder: Embedder
) -> int:
    # Embed in batches to respect provider limits.
    BATCH = 64
    rows: list[dict[str, Any]] = []
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i : i + BATCH]
        result = await embedder.embed([c.text for c in batch], input_type="document")
        for c, vec in zip(batch, result.vectors, strict=True):
            rows.append(
                {
                    "filing_id": filing_id,
                    "symbol": symbol.upper(),
                    "section_name": c.section_name,
                    "chunk_index": c.index,
                    "chunk_text": c.text,
                    "token_count": c.token_count,
                    "embedding": vec,
                    "embedding_model": result.model,
                }
            )

    # Delete-then-insert: simplest way to handle re-ingestion without a dedupe key on chunks.
    session.execute(FilingChunk.__table__.delete().where(FilingChunk.filing_id == filing_id))

    def _build(chunk_rows: list[dict[str, Any]]):
        return pg_insert(FilingChunk).values(chunk_rows)

    chunked_upsert(session, rows, build_stmt=_build, label=f"filing_chunks:{filing_id}")
    return len(rows)


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
