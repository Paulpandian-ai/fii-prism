"""idempotency_key + correlation_id on analyses.

Revision ID: 0006_hardening
Revises: 0005_decision_journal
Create Date: 2026-04-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_hardening"
down_revision: str | None = "0005_decision_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("idempotency_key", sa.String(64)))
    op.add_column("analyses", sa.Column("correlation_id", sa.String(36)))
    op.create_index(
        "ix_analyses_idempotency_key",
        "analyses",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_analyses_correlation_id", "analyses", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_analyses_correlation_id", table_name="analyses")
    op.drop_index("ix_analyses_idempotency_key", table_name="analyses")
    op.drop_column("analyses", "correlation_id")
    op.drop_column("analyses", "idempotency_key")
