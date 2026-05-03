"""specialist_cache: per-symbol cached specialist outputs.

Revision ID: 0007_specialist_cache
Revises: 0006_hardening
Create Date: 2026-05-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007_specialist_cache"
down_revision: str | None = "0006_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "specialist_cache",
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("specialist_name", sa.String(32), primary_key=True),
        sa.Column("output_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reasoning_text", sa.Text()),
        sa.Column("citations_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model_used", sa.String(32)),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Aborted-cap / synthesis_invalid / ok. Status drives the freshness gate.
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'ok'")),
        sa.Column(
            "last_run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Hash of the upstream-data fingerprint at run time. Phase 1 sets it to
        # sha256(symbol|UTC-date)[:32]; Phase 2 will incorporate price/fundamentals
        # ingestion timestamps so a fresh data load invalidates without waiting on TTL.
        sa.Column("last_input_hash", sa.String(64)),
    )
    op.create_index(
        "ix_specialist_cache_last_run_at",
        "specialist_cache",
        ["last_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_specialist_cache_last_run_at", table_name="specialist_cache")
    op.drop_table("specialist_cache")
