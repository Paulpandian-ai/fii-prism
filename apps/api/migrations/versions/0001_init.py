"""Initial migration: enable extensions.

Real tables land in Section 2. This migration just ensures the extensions exist on
any environment we deploy to (local docker-compose AND Aurora, which enables pgvector
via RDS parameter group but still needs CREATE EXTENSION).

Revision ID: 0001_init
Revises:
Create Date: 2026-04-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')


def downgrade() -> None:
    # Intentionally a no-op: dropping extensions on rollback risks data loss.
    pass
