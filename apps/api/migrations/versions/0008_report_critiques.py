"""report_critiques: uploaded analyst PDFs + their structured critiques.

Revision ID: 0008_report_critiques
Revises: 0007_specialist_cache
Create Date: 2026-05-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA, JSONB

revision: str = "0008_report_critiques"
down_revision: str | None = "0007_specialist_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_critiques",
        sa.Column("critique_id", sa.dialects.postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_filename", sa.String(255), nullable=False),
        # Free-form source label: "morningstar" / "seeking_alpha" / "sell_side" / "other".
        sa.Column("report_source", sa.String(64)),
        # sha256 of the raw PDF bytes; UNIQUE so re-upload returns the same critique.
        sa.Column("pdf_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("pdf_bytes", BYTEA, nullable=False),
        sa.Column("extracted_claims_json", JSONB),
        sa.Column("critique_json", JSONB),
        # 'pending' | 'running' | 'ok' | 'error' | 'ticker_not_found'.
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("cost_usd", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("model_used", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    # "Show me my critiques for AAPL" — symbol + most-recent first.
    op.create_index(
        "ix_report_critiques_symbol_created_at",
        "report_critiques",
        ["symbol", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_report_critiques_symbol_created_at", table_name="report_critiques")
    op.drop_table("report_critiques")
