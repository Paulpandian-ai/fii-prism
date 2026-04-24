"""Core domain schema: all 16 tables, enums, pgvector HNSW index, updated_at trigger.

Revision ID: 0002_domain_schema
Revises: 0001_init
Create Date: 2026-04-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0002_domain_schema"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Enum values (keep in sync with fii_db.enums) ------------------------------------------

_ENUMS: dict[str, list[str]] = {
    "market_cap_bucket": ["mega", "large", "mid", "small"],
    "form_type": ["10-K", "10-Q", "8-K", "4", "13F", "DEF 14A"],
    "statement_type": ["income", "balance", "cashflow", "ratios"],
    "analysis_type": ["deep_dive", "quick_refresh", "technical_only"],
    "analysis_status": ["pending", "running", "succeeded", "failed", "canceled"],
    "analysis_recommendation": ["strong_buy", "buy", "hold", "trim", "sell"],
    "confidence": ["low", "medium", "high"],
    "specialist_name": [
        "fundamentals",
        "valuation",
        "moat",
        "macro",
        "technical",
        "news",
        "insider",
        "risk",
        "bull",
        "bear",
    ],
}


def _pg_enum(name: str) -> postgresql.ENUM:
    """Return a reference to an existing Postgres ENUM type (create_type=False).

    The type itself is created exactly once up front by upgrade(). Using the
    dialect-specific postgresql.ENUM avoids Alembic's auto-CREATE behavior that
    sa.Enum(..., create_type=False) doesn't reliably suppress inside op.create_table.
    """
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


# --- Upgrade ------------------------------------------------------------------------------


def upgrade() -> None:
    # 1. Create enum types up front (once).
    for name, values in _ENUMS.items():
        value_list = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({value_list})")

    # 2. updated_at trigger function, shared across tables.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fii_set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$;
        """
    )

    # 3. Tickers — master list.
    op.create_table(
        "tickers",
        sa.Column("symbol", sa.String(10), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("sector", sa.Text),
        sa.Column("industry", sa.Text),
        sa.Column("market_cap_bucket", _pg_enum("market_cap_bucket")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("cik", sa.String(10)),
        sa.Column("first_tracked_at", sa.Date),
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
    op.create_index("ix_tickers_cik", "tickers", ["cik"])

    # 4. Prices — daily + intraday.
    op.create_table(
        "prices_daily",
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("trade_date", sa.Date, primary_key=True),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("adjusted_close", sa.Numeric(18, 6)),
        sa.Column("volume", sa.BigInteger),
        sa.Column("vwap", sa.Numeric(18, 6)),
        sa.Column("source", sa.String(32)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_prices_daily_trade_date_brin", "prices_daily", ["trade_date"], postgresql_using="brin"
    )

    op.create_table(
        "prices_intraday",
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("bar_ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("volume", sa.BigInteger),
        sa.Column("vwap", sa.Numeric(18, 6)),
        sa.Column("source", sa.String(32)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_prices_intraday_bar_ts_brin",
        "prices_intraday",
        ["bar_ts"],
        postgresql_using="brin",
    )

    # 5. Fundamentals quarterly.
    op.create_table(
        "fundamentals_quarterly",
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fiscal_period_end", sa.Date, primary_key=True),
        sa.Column("statement_type", _pg_enum("statement_type"), primary_key=True),
        sa.Column("currency", sa.String(8)),
        sa.Column("fiscal_year", sa.SmallInteger),
        sa.Column("fiscal_quarter", sa.SmallInteger),
        sa.Column("period_length_days", sa.SmallInteger),
        sa.Column("revenue", sa.Numeric(20, 2)),
        sa.Column("gross_profit", sa.Numeric(20, 2)),
        sa.Column("operating_income", sa.Numeric(20, 2)),
        sa.Column("net_income", sa.Numeric(20, 2)),
        sa.Column("eps_diluted", sa.Numeric(12, 4)),
        sa.Column("shares_outstanding", sa.Numeric(20, 2)),
        sa.Column("operating_cash_flow", sa.Numeric(20, 2)),
        sa.Column("free_cash_flow", sa.Numeric(20, 2)),
        sa.Column("capex", sa.Numeric(20, 2)),
        sa.Column("total_assets", sa.Numeric(20, 2)),
        sa.Column("total_liabilities", sa.Numeric(20, 2)),
        sa.Column("total_equity", sa.Numeric(20, 2)),
        sa.Column("total_debt", sa.Numeric(20, 2)),
        sa.Column("cash_and_equivalents", sa.Numeric(20, 2)),
        sa.Column("gross_margin", sa.Numeric(8, 4)),
        sa.Column("operating_margin", sa.Numeric(8, 4)),
        sa.Column("net_margin", sa.Numeric(8, 4)),
        sa.Column("return_on_equity", sa.Numeric(8, 4)),
        sa.Column("return_on_invested_capital", sa.Numeric(8, 4)),
        sa.Column("debt_to_equity", sa.Numeric(10, 4)),
        sa.Column("raw", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String(32)),
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

    # 6. Filings + chunks (pgvector HNSW).
    op.create_table(
        "filings",
        sa.Column(
            "filing_id",
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
        sa.Column("form_type", _pg_enum("form_type"), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_of_report", sa.Date),
        sa.Column("accession_no", sa.String(32), nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("raw_text_s3_key", sa.Text),
        sa.Column("title", sa.Text),
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
        sa.UniqueConstraint("accession_no", name="uq_filings_accession_no"),
    )
    op.create_index("ix_filings_symbol", "filings", ["symbol"])
    op.create_index("ix_filings_form_type", "filings", ["form_type"])
    op.create_index("ix_filings_filed_at", "filings", ["filed_at"])

    op.create_table(
        "filing_chunks",
        sa.Column(
            "chunk_id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "filing_id",
            UUID(as_uuid=False),
            sa.ForeignKey("filings.filing_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_name", sa.Text),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("token_count", sa.Integer),
        sa.Column("embedding", Vector(1024)),
        sa.Column("embedding_model", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_filing_chunks_filing_id", "filing_chunks", ["filing_id"])
    op.create_index("ix_filing_chunks_symbol", "filing_chunks", ["symbol"])
    op.create_index("ix_filing_chunks_section_name", "filing_chunks", ["section_name"])
    # HNSW index for cosine similarity. Index build is deferred until after bulk load
    # in practice, but creating it here means small seed loads get it for free.
    op.execute(
        """
        CREATE INDEX ix_filing_chunks_embedding_hnsw ON filing_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )

    # 7. Macro.
    op.create_table(
        "macro_series",
        sa.Column("series_id", sa.String(32), primary_key=True),
        sa.Column("observation_date", sa.Date, primary_key=True),
        sa.Column("value", sa.Numeric(24, 8)),
        sa.Column("release_id", sa.String(32)),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # 8. News (+ GIN on symbols array).
    op.create_table(
        "news_items",
        sa.Column(
            "news_id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "symbols",
            ARRAY(sa.String(10)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column("headline", sa.Text, nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("source", sa.String(64)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("sentiment_score", sa.Numeric(4, 3)),
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
        sa.CheckConstraint(
            "sentiment_score IS NULL OR (sentiment_score >= -1 AND sentiment_score <= 1)",
            name="ck_news_sentiment_range",
        ),
    )
    op.create_index("ix_news_published_at", "news_items", ["published_at"])
    op.create_index("ix_news_symbols_gin", "news_items", ["symbols"], postgresql_using="gin")

    # 9. Insiders.
    op.create_table(
        "insider_transactions",
        sa.Column(
            "id",
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
        sa.Column("insider_name", sa.Text, nullable=False),
        sa.Column("role", sa.Text),
        sa.Column("transaction_type", sa.String(16)),
        sa.Column("shares", sa.Numeric(20, 4)),
        sa.Column("price", sa.Numeric(18, 6)),
        sa.Column("transaction_date", sa.Date, nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accession_no", sa.String(32)),
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
        sa.UniqueConstraint(
            "symbol",
            "insider_name",
            "transaction_date",
            "shares",
            "transaction_type",
            name="uq_insider_dedupe",
        ),
    )
    op.create_index("ix_insider_symbol", "insider_transactions", ["symbol"])
    op.create_index("ix_insider_transaction_date", "insider_transactions", ["transaction_date"])

    # 10. Institutional holdings.
    op.create_table(
        "institutional_holdings",
        sa.Column(
            "id",
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
        sa.Column("holder_cik", sa.String(10), nullable=False),
        sa.Column("holder_name", sa.Text, nullable=False),
        sa.Column("report_period_end", sa.Date, nullable=False),
        sa.Column("shares", sa.Numeric(20, 4)),
        sa.Column("value_usd", sa.Numeric(20, 2)),
        sa.Column("accession_no", sa.String(32)),
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
        sa.UniqueConstraint(
            "holder_cik", "symbol", "report_period_end", name="uq_13f_holder_symbol_period"
        ),
    )
    op.create_index("ix_13f_symbol", "institutional_holdings", ["symbol"])
    op.create_index("ix_13f_holder_cik", "institutional_holdings", ["holder_cik"])
    op.create_index("ix_13f_report_period_end", "institutional_holdings", ["report_period_end"])

    # 11. Analyses + specialist outputs.
    op.create_table(
        "analyses",
        sa.Column(
            "analysis_id",
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
        sa.Column("analysis_type", _pg_enum("analysis_type"), nullable=False),
        sa.Column(
            "status",
            _pg_enum("analysis_status"),
            nullable=False,
            server_default=sa.text("'pending'::analysis_status"),
        ),
        sa.Column("initiated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("orchestrator_summary", sa.Text),
        sa.Column("recommendation", _pg_enum("analysis_recommendation")),
        sa.Column("confidence", _pg_enum("confidence")),
        sa.Column("fii_score", sa.Numeric(4, 2)),
        sa.Column("total_cost_usd", sa.Numeric(10, 6)),
        sa.Column("total_tokens_in", sa.Integer),
        sa.Column("total_tokens_out", sa.Integer),
        sa.Column("model_calls_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("step_functions_execution_arn", sa.Text),
        sa.Column("reasoning_trail_s3_key", sa.Text),
        sa.Column("user_notes", sa.Text),
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
        sa.CheckConstraint(
            "fii_score IS NULL OR (fii_score >= 0 AND fii_score <= 10)",
            name="ck_fii_score_range",
        ),
    )
    op.create_index("ix_analyses_symbol", "analyses", ["symbol"])

    op.create_table(
        "analysis_specialist_outputs",
        sa.Column(
            "output_id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "analysis_id",
            UUID(as_uuid=False),
            sa.ForeignKey("analyses.analysis_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("specialist_name", _pg_enum("specialist_name"), nullable=False),
        sa.Column("output_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reasoning_text", sa.Text),
        sa.Column("citations_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model_used", sa.String(64)),
        sa.Column("tokens_in", sa.Integer),
        sa.Column("tokens_out", sa.Integer),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("duration_ms", sa.Integer),
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
        sa.UniqueConstraint("analysis_id", "specialist_name", name="uq_analysis_specialist"),
    )
    op.create_index(
        "ix_specialist_outputs_analysis_id", "analysis_specialist_outputs", ["analysis_id"]
    )

    # 12. User domain.
    op.create_table(
        "watchlist",
        sa.Column("user_id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("notes", sa.Text),
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

    op.create_table(
        "positions",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("user_id", UUID(as_uuid=False), nullable=False),
        sa.Column(
            "symbol",
            sa.String(10),
            sa.ForeignKey("tickers.symbol", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("shares", sa.Numeric(20, 4), nullable=False),
        sa.Column("cost_basis", sa.Numeric(18, 6), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text),
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
    op.create_index("ix_positions_user_id", "positions", ["user_id"])
    op.create_index("ix_positions_symbol", "positions", ["symbol"])

    # 13. Prompt versioning.
    op.create_table(
        "agent_prompts",
        sa.Column("specialist_name", _pg_enum("specialist_name"), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("prompt_text", sa.Text, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text),
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

    # 14. Attach updated_at triggers to every table that has the column.
    tables_with_updated_at = [
        "tickers",
        "fundamentals_quarterly",
        "filings",
        "news_items",
        "insider_transactions",
        "institutional_holdings",
        "analyses",
        "analysis_specialist_outputs",
        "watchlist",
        "positions",
        "agent_prompts",
    ]
    for tbl in tables_with_updated_at:
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION fii_set_updated_at();
            """
        )


# --- Downgrade ----------------------------------------------------------------------------


def downgrade() -> None:
    # Drop tables in reverse dependency order.
    for tbl in [
        "agent_prompts",
        "positions",
        "watchlist",
        "analysis_specialist_outputs",
        "analyses",
        "institutional_holdings",
        "insider_transactions",
        "news_items",
        "macro_series",
        "filing_chunks",
        "filings",
        "fundamentals_quarterly",
        "prices_intraday",
        "prices_daily",
        "tickers",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")

    op.execute("DROP FUNCTION IF EXISTS fii_set_updated_at() CASCADE")

    for name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")
