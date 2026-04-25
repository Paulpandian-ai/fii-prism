"""Each specialist's fake-mode path produces a schema-valid output.

Fake mode reads from real Postgres (no LLM call), so this is the closest we can
get to "golden test" coverage without an Anthropic API key. It catches:
  - Schema drift between specialist construction and the Pydantic shape
  - Bugs in fake-mode tool plumbing
  - Off-by-one issues in citation wrapping
"""

from __future__ import annotations

import asyncio

import pytest
from fii_agents.model import Model
from fii_agents.specialists.base import SpecialistContext
from fii_agents.specialists.debate import BearResearcher, BullResearcher
from fii_agents.specialists.fundamentals import FundamentalsSpecialist
from fii_agents.specialists.insider import InsiderFlowSpecialist
from fii_agents.specialists.macro import MacroSpecialist
from fii_agents.specialists.moat import MoatSpecialist
from fii_agents.specialists.news import NewsSentimentSpecialist
from fii_agents.specialists.risk import RiskSpecialist
from fii_agents.specialists.technical import TechnicalSpecialist
from fii_agents.specialists.valuation import ValuationSpecialist
from fii_data_clients.embeddings import EMBEDDING_DIM, EmbeddingResult


class _Embedder:
    model = "shim"

    async def embed(self, texts, *, input_type: str = "document"):
        return EmbeddingResult(vectors=[[0.0] * EMBEDDING_DIM for _ in texts], model=self.model)


def _ctx(session_factory, **prior) -> SpecialistContext:
    return SpecialistContext(
        symbol="AAPL",
        analysis_id="00000000-0000-0000-0000-000000000000",
        user_id="00000000-0000-0000-0000-000000000000",
        factory=session_factory,
        embedder=_Embedder(),
        raw_bucket=None,
        prior=prior,
    )


@pytest.fixture
def fake_model():
    return Model()  # FII_USE_FAKE_MODEL=1 is set by conftest's autouse fixture


SOLO_SPECIALISTS = [
    FundamentalsSpecialist,
    ValuationSpecialist,
    MoatSpecialist,
    MacroSpecialist,
    TechnicalSpecialist,
    NewsSentimentSpecialist,
    InsiderFlowSpecialist,
    RiskSpecialist,
]


@pytest.mark.parametrize("cls", SOLO_SPECIALISTS, ids=lambda c: c.__name__)
def test_specialist_fake_mode_produces_valid_output(session_factory, fake_model, cls):
    ctx = _ctx(session_factory)
    spec = cls()
    result = asyncio.get_event_loop().run_until_complete(spec.run(ctx, fake_model))
    assert result.error is None, result.error
    assert result.output is not None, "fake mode must return a populated output"


def test_bull_and_bear_with_priors(session_factory, fake_model):
    # Build a minimal prior bag so the debate has source_ids to reference.
    fund = asyncio.get_event_loop().run_until_complete(
        FundamentalsSpecialist().run(_ctx(session_factory), fake_model)
    )
    moat = asyncio.get_event_loop().run_until_complete(
        MoatSpecialist().run(_ctx(session_factory), fake_model)
    )
    prior = {"fundamentals": fund.output, "moat": moat.output}

    bull = asyncio.get_event_loop().run_until_complete(
        BullResearcher().run(_ctx(session_factory, **prior), fake_model)
    )
    bear = asyncio.get_event_loop().run_until_complete(
        BearResearcher().run(_ctx(session_factory, **prior), fake_model)
    )
    assert bull.output is not None
    assert bear.output is not None
    assert len(bull.output.strongest_evidence) >= 1
    assert len(bear.output.strongest_evidence) >= 1
