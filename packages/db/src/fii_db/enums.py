"""Domain enums. These become Postgres ENUM types via SQLAlchemy + Alembic."""

from enum import StrEnum


class MarketCapBucket(StrEnum):
    MEGA = "mega"  # > $200B
    LARGE = "large"  # $10B - $200B
    MID = "mid"  # $2B - $10B
    SMALL = "small"  # < $2B


class FormType(StrEnum):
    TEN_K = "10-K"
    TEN_Q = "10-Q"
    EIGHT_K = "8-K"
    FORM_4 = "4"
    THIRTEEN_F = "13F"
    PROXY = "DEF 14A"


class StatementType(StrEnum):
    INCOME = "income"
    BALANCE = "balance"
    CASHFLOW = "cashflow"
    RATIOS = "ratios"


class AnalysisType(StrEnum):
    DEEP_DIVE = "deep_dive"
    QUICK_REFRESH = "quick_refresh"
    TECHNICAL_ONLY = "technical_only"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class AnalysisRecommendation(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    TRIM = "trim"
    SELL = "sell"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SpecialistName(StrEnum):
    FUNDAMENTALS = "fundamentals"
    VALUATION = "valuation"
    MOAT = "moat"
    MACRO = "macro"
    TECHNICAL = "technical"
    NEWS = "news"
    INSIDER = "insider"
    RISK = "risk"
    BULL = "bull"
    BEAR = "bear"
