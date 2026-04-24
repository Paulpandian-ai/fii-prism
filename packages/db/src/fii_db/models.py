"""SQLAlchemy models — canonical shape of the FII-PRISM domain.

Conventions:
- UUID primary keys use uuid_generate_v4() (the extension is enabled by migration 0001).
- Every mutable table carries created_at + updated_at (server-set; updated_at via ORM event).
- Time-series tables (prices, macro) use composite natural keys; append-mostly.
- Text columns default to unbounded TEXT unless there's a good reason to cap.
- JSON payloads use JSONB for indexability.
- Embeddings use pgvector Vector(1024) to match Voyage voyage-finance-2 output.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy import (
    Enum as PgEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fii_db.enums import (
    AnalysisRecommendation,
    AnalysisStatus,
    AnalysisType,
    Confidence,
    FormType,
    MarketCapBucket,
    SpecialistName,
    StatementType,
)

# --- Base & mixins ------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all FII-PRISM tables."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


def _uuid_pk() -> Mapped[str]:
    return mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.uuid_generate_v4(),
    )


# --- Reference data -----------------------------------------------------------------------


class Ticker(Base, TimestampMixin):
    __tablename__ = "tickers"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    market_cap_bucket: Mapped[MarketCapBucket | None] = mapped_column(
        PgEnum(MarketCapBucket, name="market_cap_bucket", native_enum=True),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    cik: Mapped[str | None] = mapped_column(String(10), index=True)
    first_tracked_at: Mapped[date | None] = mapped_column(Date)


# --- Prices -------------------------------------------------------------------------------


class PriceDaily(Base):
    """Daily OHLCV. Composite PK; BRIN on trade_date (cheap for append-only time-series)."""

    __tablename__ = "prices_daily"

    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), primary_key=True
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    adjusted_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source: Mapped[str | None] = mapped_column(String(32))  # 'polygon', 'fmp', etc.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_prices_daily_trade_date_brin", "trade_date", postgresql_using="brin"),
    )


class PriceIntraday(Base):
    """1-minute bars, rolling 7-day window. Nightly job drops older rows."""

    __tablename__ = "prices_intraday"

    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), primary_key=True
    )
    bar_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_prices_intraday_bar_ts_brin", "bar_ts", postgresql_using="brin"),)


# --- Fundamentals & filings ---------------------------------------------------------------


class FundamentalsQuarterly(Base, TimestampMixin):
    """Quarterly statements. JSONB for the raw payload; hot columns extracted for queryability.

    Composite PK lets us store income/balance/cashflow/ratios separately per period.
    """

    __tablename__ = "fundamentals_quarterly"

    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), primary_key=True
    )
    fiscal_period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    statement_type: Mapped[StatementType] = mapped_column(
        PgEnum(StatementType, name="statement_type", native_enum=True), primary_key=True
    )

    currency: Mapped[str | None] = mapped_column(String(8))
    fiscal_year: Mapped[int | None] = mapped_column(SmallInteger)
    fiscal_quarter: Mapped[int | None] = mapped_column(SmallInteger)
    period_length_days: Mapped[int | None] = mapped_column(SmallInteger)

    # 20 most-queried fields extracted from the raw payload. All optional — not every
    # statement type has every field.
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    operating_income: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    net_income: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    eps_diluted: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    operating_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    free_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    capex: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_equity: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    cash_and_equivalents: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    operating_margin: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    net_margin: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    return_on_equity: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    return_on_invested_capital: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    debt_to_equity: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    source: Mapped[str | None] = mapped_column(String(32))


class Filing(Base, TimestampMixin):
    """SEC filing metadata. Raw text goes to S3; pointer lives here."""

    __tablename__ = "filings"

    filing_id: Mapped[str] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False, index=True
    )
    form_type: Mapped[FormType] = mapped_column(
        PgEnum(FormType, name="form_type", native_enum=True), nullable=False, index=True
    )
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    period_of_report: Mapped[date | None] = mapped_column(Date)
    accession_no: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    raw_text_s3_key: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)

    chunks: Mapped[list[FilingChunk]] = relationship(
        back_populates="filing", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (UniqueConstraint("accession_no", name="uq_filings_accession_no"),)


class FilingChunk(Base):
    """Chunked filing text for RAG. Embedding is Vector(1024) — Voyage voyage-finance-2."""

    __tablename__ = "filing_chunks"

    chunk_id: Mapped[str] = _uuid_pk()
    filing_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("filings.filing_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False, index=True
    )
    section_name: Mapped[str | None] = mapped_column(Text, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    filing: Mapped[Filing] = relationship(back_populates="chunks")

    # HNSW index for cosine similarity. Tuned with reasonable defaults; we can rebuild later.
    __table_args__ = (
        Index(
            "ix_filing_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# --- Macro, news, insiders ----------------------------------------------------------------


class MacroSeries(Base):
    """FRED time-series observations."""

    __tablename__ = "macro_series"

    series_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    release_id: Mapped[str | None] = mapped_column(String(32))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NewsItem(Base, TimestampMixin):
    """Deduplicated news. Sentiment is populated lazily by the News agent, not at ingest."""

    __tablename__ = "news_items"

    news_id: Mapped[str] = _uuid_pk()
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    symbols: Mapped[list[str]] = mapped_column(
        ARRAY(String(10)), nullable=False, server_default="{}"
    )
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sentiment_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    __table_args__ = (
        CheckConstraint(
            "sentiment_score IS NULL OR (sentiment_score >= -1 AND sentiment_score <= 1)",
            name="ck_news_sentiment_range",
        ),
        Index("ix_news_symbols_gin", "symbols", postgresql_using="gin"),
    )


class InsiderTransaction(Base, TimestampMixin):
    __tablename__ = "insider_transactions"

    id: Mapped[str] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False, index=True
    )
    insider_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    transaction_type: Mapped[str | None] = mapped_column(String(16))  # P, S, A, F, M, etc.
    shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accession_no: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "insider_name",
            "transaction_date",
            "shares",
            "transaction_type",
            name="uq_insider_dedupe",
        ),
    )


class InstitutionalHolding(Base, TimestampMixin):
    """13F snapshots — one row per (holder, symbol, quarter)."""

    __tablename__ = "institutional_holdings"

    id: Mapped[str] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False, index=True
    )
    holder_cik: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    holder_name: Mapped[str] = mapped_column(Text, nullable=False)
    report_period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    value_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    accession_no: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "holder_cik", "symbol", "report_period_end", name="uq_13f_holder_symbol_period"
        ),
    )


# --- Analyses -----------------------------------------------------------------------------


class Analysis(Base, TimestampMixin):
    """Per-run top-level record. Full reasoning trail lives in S3; this is the index."""

    __tablename__ = "analyses"

    analysis_id: Mapped[str] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_type: Mapped[AnalysisType] = mapped_column(
        PgEnum(AnalysisType, name="analysis_type", native_enum=True), nullable=False
    )
    status: Mapped[AnalysisStatus] = mapped_column(
        PgEnum(AnalysisStatus, name="analysis_status", native_enum=True),
        nullable=False,
        server_default=AnalysisStatus.PENDING.value,
    )
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    orchestrator_summary: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[AnalysisRecommendation | None] = mapped_column(
        PgEnum(AnalysisRecommendation, name="analysis_recommendation", native_enum=True)
    )
    confidence: Mapped[Confidence | None] = mapped_column(
        PgEnum(Confidence, name="confidence", native_enum=True)
    )
    fii_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))

    total_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    total_tokens_in: Mapped[int | None] = mapped_column(Integer)
    total_tokens_out: Mapped[int | None] = mapped_column(Integer)
    model_calls_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    step_functions_execution_arn: Mapped[str | None] = mapped_column(Text)
    reasoning_trail_s3_key: Mapped[str | None] = mapped_column(Text)
    user_notes: Mapped[str | None] = mapped_column(Text)

    specialist_outputs: Mapped[list[AnalysisSpecialistOutput]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "fii_score IS NULL OR (fii_score >= 0 AND fii_score <= 10)", name="ck_fii_score_range"
        ),
    )


class AnalysisSpecialistOutput(Base, TimestampMixin):
    __tablename__ = "analysis_specialist_outputs"

    output_id: Mapped[str] = _uuid_pk()
    analysis_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("analyses.analysis_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    specialist_name: Mapped[SpecialistName] = mapped_column(
        PgEnum(SpecialistName, name="specialist_name", native_enum=True), nullable=False
    )

    output_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    reasoning_text: Mapped[str | None] = mapped_column(Text)
    citations_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    model_used: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    analysis: Mapped[Analysis] = relationship(back_populates="specialist_outputs")

    __table_args__ = (
        UniqueConstraint("analysis_id", "specialist_name", name="uq_analysis_specialist"),
    )


# --- User domain (single-user MVP; user_id reserved per Section 0) ------------------------

# UUID NIL used as the single-user sentinel until Cognito multi-tenancy lands.
SINGLE_USER_ID = "00000000-0000-0000-0000-000000000000"


class Watchlist(Base, TimestampMixin):
    __tablename__ = "watchlist"

    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)


class Position(Base, TimestampMixin):
    __tablename__ = "positions"

    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("tickers.symbol", ondelete="CASCADE"), nullable=False, index=True
    )
    shares: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


# --- Prompt versioning --------------------------------------------------------------------


class AgentPrompt(Base, TimestampMixin):
    """Versioned system prompts per specialist. Composite PK supports A/B + rollback."""

    __tablename__ = "agent_prompts"

    specialist_name: Mapped[SpecialistName] = mapped_column(
        PgEnum(SpecialistName, name="specialist_name", create_type=False, native_enum=True),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)

    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(Text)


# --- Auto-bump updated_at on UPDATE for tables that have the mixin ------------------------


@event.listens_for(Base.metadata, "before_create")
def _ensure_update_trigger(target, connection, **_kw):
    # Create a shared updated_at trigger function once. Per-table triggers are attached
    # in the Alembic migration (see migration 0002) — not here, to keep model import pure.
    pass
