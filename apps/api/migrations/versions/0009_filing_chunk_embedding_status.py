"""filing_chunks.embedding_status: track whether the row's vector is populated.

Revision ID: 0009_filing_chunk_embedding_status
Revises: 0008_report_critiques
Create Date: 2026-05-04

The embedder (Voyage / Bedrock) is the slowest + most failure-prone step in
the filing ingest pipeline. Previously a Voyage rate-limit during chunk
embedding rolled back the whole filing transaction — losing the EDGAR fetch
plus the chunk text. We now persist the Filing row + chunk text in one
transaction and embed in a second. `embedding_status` tracks which chunks
are still pending, so a backfill job (`fii-ingest backfill-embeddings`)
can re-attempt them without re-fetching.

States: 'pending' (no vector yet) | 'ok' (vector populated). We deliberately
do NOT add a 'failed' state — failure detail lives in logs and a `pending`
row is fair game for the next backfill attempt regardless of why it's
pending.

Backfill on upgrade: every existing row gets 'ok' if it has a vector,
'pending' otherwise. That preserves the meaning of the existing data
without forcing a re-embed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_chunk_embed_status"
down_revision: str | None = "0008_report_critiques"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "filing_chunks",
        sa.Column(
            "embedding_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    # Backfill: existing rows with a populated embedding are 'ok'; rows
    # without (shouldn't exist today but is the only correct default) stay
    # 'pending' from the server_default.
    op.execute(
        "UPDATE filing_chunks SET embedding_status = 'ok' WHERE embedding IS NOT NULL"
    )
    # Partial index — backfill scans only pending rows. Filtering on this
    # keeps the index tiny in steady-state when most rows are 'ok'.
    op.create_index(
        "ix_filing_chunks_embedding_pending",
        "filing_chunks",
        ["filing_id"],
        postgresql_where=sa.text("embedding_status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_filing_chunks_embedding_pending", table_name="filing_chunks")
    op.drop_column("filing_chunks", "embedding_status")
