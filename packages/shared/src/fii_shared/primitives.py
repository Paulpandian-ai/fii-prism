"""Citation and primitive types used by every specialist output.

These are the contract pieces that prevent hallucination:
- SourceRef — identifies where a claim comes from
- CitedClaim — a textual claim plus its sources
- CitedNumber — a numeric value that MUST carry a source

Numbers outside a CitedNumber wrapper are allowed only for agent-computed metrics
(e.g. RSI, realized vol) where the underlying series IS the source.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Trend3(StrEnum):
    IMPROVING = "improving"
    STABLE = "stable"
    DETERIORATING = "deteriorating"


class SourceType(StrEnum):
    SEC_FILING = "sec_filing"
    FMP_FUNDAMENTAL = "fmp_fundamental"
    POLYGON_PRICE = "polygon_price"
    FRED_SERIES = "fred_series"
    FINNHUB_NEWS = "finnhub_news"
    CALCULATED = "calculated"


class SourceRef(BaseModel):
    """Where a claim comes from. Every factual assertion an agent makes must carry one."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    source_type: SourceType
    source_id: str = Field(
        min_length=1,
        description="Filing accession, FMP endpoint, FRED series ID, news URL, or formula name",
    )
    section: str | None = Field(
        default=None, description="e.g. 'Item 1A Risk Factors' or 'ratios.debtEquityRatio'"
    )
    retrieved_at: datetime
    url: HttpUrl | None = None


class CitedClaim(BaseModel):
    """A textual claim with at least one source. Used for qualitative evidence."""

    model_config = ConfigDict(str_strip_whitespace=True)

    claim: str = Field(min_length=1, max_length=2000)
    sources: list[SourceRef] = Field(min_length=1)
    confidence: Confidence


class CitedNumber(BaseModel):
    """A numeric value bound to exactly one source. `source` is non-optional — if JSON
    supplies null, Pydantic rejects. This is the primary defense against made-up numbers.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    value: float
    unit: str = Field(
        min_length=1,
        max_length=32,
        description="'USD', 'percent', 'ratio', 'count', 'months', etc.",
    )
    as_of: date
    source: SourceRef  # intentionally required, no | None


# --- Shared sub-types used across specialists --------------------------------------------


class CompParable(BaseModel):
    """One row of the valuation comparable-multiples table."""

    peer_symbol: str = Field(min_length=1, max_length=10)
    ev_ebitda: float | None = None
    pe_ratio: float | None = None


class InsiderMove(BaseModel):
    name: str = Field(min_length=1)
    role: str | None = None
    action: Literal["buy", "sell", "grant", "exercise", "other"]
    shares: float
    dollar_value: float
    date: date


class FiveForces(BaseModel):
    threat_new_entrants: Literal["low", "medium", "high"]
    bargaining_buyers: Literal["low", "medium", "high"]
    bargaining_suppliers: Literal["low", "medium", "high"]
    threat_substitutes: Literal["low", "medium", "high"]
    competitive_rivalry: Literal["low", "medium", "high"]
