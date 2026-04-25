"""chat_sessions, chat_messages, user_settings.

Revision ID: 0004_advisor_chat
Revises: 0003_refresh_events
Create Date: 2026-04-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0004_advisor_chat"
down_revision: str | None = "0003_refresh_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column(
            "session_id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("user_id", UUID(as_uuid=False), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=sa.text("'New chat'")),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(10, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("total_tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    op.create_index("ix_chat_sessions_updated_at", "chat_sessions", [sa.text("updated_at DESC")])

    op.create_table(
        "chat_messages",
        sa.Column(
            "message_id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=False),
            sa.ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),  # 'user' | 'assistant' | 'tool'
        sa.Column("content", sa.Text()),
        sa.Column("tool_calls", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "referenced_analysis_ids",
            ARRAY(UUID(as_uuid=False)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cost_usd",
            sa.Numeric(10, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("model_used", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["session_id", "created_at"])

    op.create_table(
        "user_settings",
        sa.Column("user_id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "cash_balance_usd",
            sa.Numeric(20, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "target_concentration_pct",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("10.00"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
