"""Shared types for specialists.

Each specialist has a `run(ctx) -> SpecialistResult` coroutine. The orchestrator
node wrapper handles budget checks, error capture, and state mutation — specialists
just return a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from fii_data_clients import Embedder
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker

from fii_agents.model import Model


@dataclass
class SpecialistContext:
    symbol: str
    analysis_id: str
    user_id: str
    factory: sessionmaker
    embedder: Embedder
    raw_bucket: str | None
    context: dict[str, Any] = field(default_factory=dict)
    # Upstream specialist outputs (for Bull/Bear/Risk that read earlier results).
    prior: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpecialistResult:
    output: BaseModel | None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    model_calls: int = 0
    duration_ms: int = 0
    error: str | None = None
    # 'ok' on a clean run, 'aborted_cap' when a per-specialist call/cost cap
    # short-circuits the loop. The runner persists this onto specialist_cache.status
    # so synthesis's freshness gate can refuse to use partial work.
    status: str = "ok"


class Specialist(Protocol):
    name: str

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult: ...
