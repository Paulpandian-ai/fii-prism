"""refresh_events + event_type enum.

Revision ID: 0003_refresh_events
Revises: 0002_domain_schema
Create Date: 2026-04-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0003_refresh_events"
down_revision: str | None = "0002_domain_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVENT_TYPES = (
    "price_shock",
    "news_shock",
    "8k_filed",
    "earnings_release",
    "macro_surprise",
)


def upgrade() -> None:
    values = ", ".join(f"'{t}'" for t in EVENT_TYPES)
    op.execute(f"CREATE TYPE event_type AS ENUM ({values})")
    op.create_table(
        "refresh_events",
        sa.Column(
            "event_id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            postgresql.ENUM(*EVENT_TYPES, name="event_type", create_type=False),
            nullable=False,
        ),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "analysis_id",
            UUID(as_uuid=False),
            sa.ForeignKey("analyses.analysis_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "dedupe_key",
            sa.String(128),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_refresh_events_symbol", "refresh_events", ["symbol"])
    op.create_index("ix_refresh_events_detected_at", "refresh_events", ["detected_at"])
    op.create_index(
        "ix_refresh_events_dedupe",
        "refresh_events",
        ["symbol", "event_type", "detected_at"],
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_events CASCADE")
    op.execute("DROP TYPE IF EXISTS event_type CASCADE")
