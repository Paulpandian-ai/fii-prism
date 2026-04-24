"""Acceptance criterion 1 + 2: every model round-trips through JSON and produces a schema.

For each schema in the fixture, we:
  1. Parse it (Pydantic validates).
  2. Dump back to JSON-safe dict with model_dump(mode="json").
  3. Re-parse to confirm idempotence.
  4. Confirm model_json_schema() produces valid JSON Schema.
"""

from __future__ import annotations

import json

import pytest
from fii_shared import (
    BullBearDebateOutput,
    FundamentalsOutput,
    InsiderFlowOutput,
    MacroOutput,
    MoatOutput,
    NewsSentimentOutput,
    OrchestratorFinalOutput,
    RiskOutput,
    TechnicalOutput,
    ValuationOutput,
)
from pydantic import BaseModel

_MODELS: dict[str, type[BaseModel]] = {
    "FundamentalsOutput": FundamentalsOutput,
    "ValuationOutput": ValuationOutput,
    "MoatOutput": MoatOutput,
    "MacroOutput": MacroOutput,
    "TechnicalOutput": TechnicalOutput,
    "NewsSentimentOutput": NewsSentimentOutput,
    "InsiderFlowOutput": InsiderFlowOutput,
    "RiskOutput": RiskOutput,
    "BullBearDebateOutput": BullBearDebateOutput,
    "OrchestratorFinalOutput": OrchestratorFinalOutput,
}


@pytest.mark.parametrize("name,model", list(_MODELS.items()))
def test_round_trip_each_schema(valid_outputs, name: str, model: type[BaseModel]) -> None:
    payload = valid_outputs[name]
    parsed = model.model_validate(payload)
    dumped = json.loads(parsed.model_dump_json())
    reparsed = model.model_validate(dumped)
    assert reparsed.model_dump_json() == parsed.model_dump_json()


@pytest.mark.parametrize("name,model", list(_MODELS.items()))
def test_json_schema_emits(name: str, model: type[BaseModel]) -> None:
    schema = model.model_json_schema()
    # Must at minimum have properties and type
    assert schema["type"] == "object"
    assert "properties" in schema
    assert schema["properties"], f"{name} must have at least one property"
