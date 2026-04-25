"""decision journal: action_taken on analyses + analysis_outcomes table.

Revision ID: 0005_decision_journal
Revises: 0004_advisor_chat
Create Date: 2026-04-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0005_decision_journal"
down_revision: str | None = "0004_advisor_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTION_TAKEN_VALUES = (
    "none",
    "bought",
    "added",
    "held",
    "trimmed",
    "sold",
    "paper_bought",
    "paper_sold",
)


def upgrade() -> None:
    # action_taken enum + analyses columns.
    values = ", ".join(f"'{v}'" for v in ACTION_TAKEN_VALUES)
    op.execute(f"CREATE TYPE action_taken AS ENUM ({values})")

    op.add_column(
        "analyses",
        sa.Column(
            "action_taken",
            postgresql.ENUM(*ACTION_TAKEN_VALUES, name="action_taken", create_type=False),
            nullable=False,
            server_default=sa.text("'none'::action_taken"),
        ),
    )
    op.add_column("analyses", sa.Column("action_size_usd", sa.Numeric(20, 2)))
    op.add_column("analyses", sa.Column("action_price", sa.Numeric(18, 6)))
    op.add_column("analyses", sa.Column("action_at", sa.DateTime(timezone=True)))
    op.add_column("analyses", sa.Column("action_notes", sa.Text()))
    op.add_column(
        "analyses",
        sa.Column(
            "prompt_versions_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_analyses_action_taken", "analyses", ["action_taken"])

    # analysis_outcomes — one row per (analysis_id) once we have horizon data.
    op.create_table(
        "analysis_outcomes",
        sa.Column(
            "analysis_id",
            UUID(as_uuid=False),
            sa.ForeignKey("analyses.analysis_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("symbol", sa.String(10), nullable=False, index=True),
        sa.Column("entry_price", sa.Numeric(18, 6)),
        sa.Column("entry_date", sa.Date()),
        # Per-horizon absolute returns + alpha vs SPY + hit/miss flags.
        sa.Column("return_1d", sa.Numeric(8, 5)),
        sa.Column("return_1w", sa.Numeric(8, 5)),
        sa.Column("return_1m", sa.Numeric(8, 5)),
        sa.Column("return_3m", sa.Numeric(8, 5)),
        sa.Column("return_6m", sa.Numeric(8, 5)),
        sa.Column("return_1y", sa.Numeric(8, 5)),
        sa.Column("alpha_1d", sa.Numeric(8, 5)),
        sa.Column("alpha_1w", sa.Numeric(8, 5)),
        sa.Column("alpha_1m", sa.Numeric(8, 5)),
        sa.Column("alpha_3m", sa.Numeric(8, 5)),
        sa.Column("alpha_6m", sa.Numeric(8, 5)),
        sa.Column("alpha_1y", sa.Numeric(8, 5)),
        sa.Column("hit_1d", sa.Boolean()),
        sa.Column("hit_1w", sa.Boolean()),
        sa.Column("hit_1m", sa.Boolean()),
        sa.Column("hit_3m", sa.Boolean()),
        sa.Column("hit_6m", sa.Boolean()),
        sa.Column("hit_1y", sa.Boolean()),
        # Specialist dominance label computed at outcome time so dashboards can pivot
        # without re-deriving from specialist_outputs.
        sa.Column("dominance", sa.String(16)),  # bull | bear | balanced
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "spy_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("ix_outcomes_symbol", "analysis_outcomes", ["symbol"])


def downgrade() -> None:
    op.drop_table("analysis_outcomes")
    op.drop_index("ix_analyses_action_taken", table_name="analyses")
    op.drop_column("analyses", "prompt_versions_json")
    op.drop_column("analyses", "action_notes")
    op.drop_column("analyses", "action_at")
    op.drop_column("analyses", "action_price")
    op.drop_column("analyses", "action_size_usd")
    op.drop_column("analyses", "action_taken")
    op.execute("DROP TYPE IF EXISTS action_taken CASCADE")
