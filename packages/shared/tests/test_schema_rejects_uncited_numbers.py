"""Acceptance criterion 3: the schema rejects a FundamentalsOutput where
`revenue_ttm.source` is null.

This is the single most important test — it's the guarantee that keeps the agents honest.
"""

from __future__ import annotations

import copy
from datetime import UTC

import pytest
from fii_shared import FundamentalsOutput
from fii_shared.validation import ReprompTicket, try_parse
from pydantic import ValidationError


def test_rejects_null_source(valid_outputs) -> None:
    bad = copy.deepcopy(valid_outputs["FundamentalsOutput"])
    bad["revenue_ttm"]["source"] = None  # the violation

    with pytest.raises(ValidationError) as exc:
        FundamentalsOutput.model_validate(bad)

    # Error message should mention the offending path.
    payload = exc.value.errors()
    paths = [".".join(str(p) for p in e["loc"]) for e in payload]
    assert any("revenue_ttm.source" in p for p in paths), paths


def test_rejects_missing_source_field(valid_outputs) -> None:
    bad = copy.deepcopy(valid_outputs["FundamentalsOutput"])
    del bad["revenue_ttm"]["source"]

    with pytest.raises(ValidationError):
        FundamentalsOutput.model_validate(bad)


def test_try_parse_returns_ticket_on_null_source(valid_outputs) -> None:
    """Rejection-sampling helper wraps the error for Claude re-prompting."""
    bad = copy.deepcopy(valid_outputs["FundamentalsOutput"])
    bad["revenue_ttm"]["source"] = None

    result = try_parse(FundamentalsOutput, bad)
    assert isinstance(result, ReprompTicket)
    assert result.model_name == "FundamentalsOutput"
    assert any("revenue_ttm.source" in e for e in result.errors)
    assert "schema validation" in result.as_prompt().lower()


def test_cited_claim_requires_at_least_one_source() -> None:
    """CitedClaim.sources has min_length=1 — empty list is a violation."""
    from datetime import datetime

    from fii_shared import CitedClaim

    with pytest.raises(ValidationError):
        CitedClaim.model_validate({"claim": "x", "sources": [], "confidence": "low"})

    # Spot-check that a single source passes.
    ok = CitedClaim.model_validate(
        {
            "claim": "x",
            "sources": [
                {
                    "source_type": "calculated",
                    "source_id": "y",
                    "section": None,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "url": None,
                }
            ],
            "confidence": "low",
        }
    )
    assert ok.sources[0].source_id == "y"
