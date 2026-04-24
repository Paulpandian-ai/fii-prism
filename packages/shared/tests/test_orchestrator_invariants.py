"""OrchestratorFinalOutput-specific invariants:
- what_could_make_me_wrong must have at least 3 items
- recommendation buy/strong_buy requires what_i_would_buy non-empty
- disclaimer must be exactly the educational string
"""

from __future__ import annotations

import copy

import pytest
from fii_shared import OrchestratorFinalOutput
from pydantic import ValidationError


def test_what_could_make_me_wrong_min_3(valid_outputs) -> None:
    bad = copy.deepcopy(valid_outputs["OrchestratorFinalOutput"])
    bad["what_could_make_me_wrong"] = bad["what_could_make_me_wrong"][:2]

    with pytest.raises(ValidationError) as exc:
        OrchestratorFinalOutput.model_validate(bad)
    assert any(
        "what_could_make_me_wrong" in ".".join(str(p) for p in e["loc"]) for e in exc.value.errors()
    )


def test_buy_requires_what_i_would_buy(valid_outputs) -> None:
    bad = copy.deepcopy(valid_outputs["OrchestratorFinalOutput"])
    bad["recommendation"] = "buy"
    bad["what_i_would_buy"] = None  # must be filled when buying

    with pytest.raises(ValidationError) as exc:
        OrchestratorFinalOutput.model_validate(bad)
    assert "what_i_would_buy" in str(exc.value)


def test_disclaimer_must_match(valid_outputs) -> None:
    bad = copy.deepcopy(valid_outputs["OrchestratorFinalOutput"])
    bad["disclaimer"] = "Consult your financial advisor."

    with pytest.raises(ValidationError):
        OrchestratorFinalOutput.model_validate(bad)
