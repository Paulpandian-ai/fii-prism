"""Canonical domain schemas.

Kept deliberately thin at MVP — we'll grow this as Sections 2+ flesh out the data model.
Mirrored by the TypeScript zod schemas in packages/shared/src/schemas.ts.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class Ticker(BaseModel):
    """Canonical identifier for an equity. Symbol is normalized to upper-case."""

    symbol: str = Field(min_length=1, max_length=10)
    exchange: str | None = Field(default=None, max_length=16)


class DeepDiveRequest(BaseModel):
    """Request to kick off a full specialist-swarm analysis for a single ticker."""

    ticker: Ticker
    horizon_months: int = Field(default=36, ge=1, le=240)
    notes: str | None = Field(default=None, max_length=2000)


class AnalysisRun(BaseModel):
    """Metadata for one deep-dive execution. Full reasoning trail lives in S3."""

    run_id: str
    ticker: Ticker
    status: AgentRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    step_functions_execution_arn: str | None = None
    cost_usd: float | None = Field(default=None, ge=0)
