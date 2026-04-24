"""The qualitative_summary numeric-claim validator.

Rule: any factual-looking number in prose (unit-suffixed or decimal) must either be
  (a) wrapped as [value:source_id], or
  (b) match a CitedNumber.value on the same model.
"""

from __future__ import annotations

import copy
from datetime import UTC

import pytest
from fii_shared import MoatOutput
from fii_shared.validation import validate_numeric_claims
from pydantic import ValidationError


def test_rejects_uncited_percent_in_summary(valid_outputs) -> None:
    bad = copy.deepcopy(valid_outputs["MoatOutput"])
    bad["qualitative_summary"] = (
        "Market share held steady. Services grew 37% year over year, a notable expansion."
    )

    with pytest.raises(ValidationError) as exc:
        MoatOutput.model_validate(bad)

    msg = str(exc.value)
    assert "Uncited numeric claims" in msg or "uncited" in msg.lower()
    assert "37" in msg


def test_accepts_bracketed_citation(valid_outputs) -> None:
    ok = copy.deepcopy(valid_outputs["MoatOutput"])
    # [37%:services_growth] is the wrapped form.
    ok["qualitative_summary"] = (
        "Services revenue grew [37%:fmp/income/AAPL/services_segment] over the last year, "
        "supporting the moat trend."
    )
    MoatOutput.model_validate(ok)  # should not raise


def test_allows_years_in_prose(valid_outputs) -> None:
    ok = copy.deepcopy(valid_outputs["MoatOutput"])
    ok["qualitative_summary"] = (
        "The moat has been stable from 2020 through 2024, with no material shifts."
    )
    MoatOutput.model_validate(ok)  # 2020 and 2024 are years, whitelisted


def test_matches_cited_number_value() -> None:
    """A number that exactly matches a CitedNumber.value elsewhere in the model passes."""
    from fii_shared import FundamentalsOutput

    payload = {
        "symbol": "AAPL",
        "revenue_ttm": {
            "value": 391.0,
            "unit": "USD",
            "as_of": "2024-09-28",
            "source": {
                "source_type": "fmp_fundamental",
                "source_id": "x",
                "section": None,
                "retrieved_at": "2026-04-24T12:00:00Z",
                "url": None,
            },
        },
        "revenue_cagr_3y": _cited(0.05),
        "gross_margin_trend": "stable",
        "gross_margin_latest": _cited(0.46),
        "operating_margin_latest": _cited(0.31),
        "fcf_conversion": _cited(1.10),
        "net_debt_to_ebitda": _cited(-0.5),
        "roic": _cited(0.44),
        "roic_vs_wacc_spread": _cited(0.35),
        "interest_coverage": _cited(28.0),
        "qualitative_summary": (
            "Revenue TTM is 391.0 billion with solid margins and cash conversion."
        ),
        "confidence": "high",
    }
    FundamentalsOutput.model_validate(payload)  # 391.0 matches revenue_ttm.value


def _cited(v: float) -> dict:
    return {
        "value": v,
        "unit": "ratio",
        "as_of": "2024-09-28",
        "source": {
            "source_type": "calculated",
            "source_id": "x",
            "section": None,
            "retrieved_at": "2026-04-24T12:00:00Z",
            "url": None,
        },
    }


def test_validate_helper_returns_offenses() -> None:
    """The function itself (used directly) lists tokens that failed."""
    from fii_shared import Confidence, FiveForces, MoatOutput

    # Build a minimal model skipping the post-init validator by monkey-setting a bad summary
    # after construction. Easier: call the helper with an already-built model.
    five = FiveForces(
        threat_new_entrants="low",
        bargaining_buyers="low",
        bargaining_suppliers="low",
        threat_substitutes="low",
        competitive_rivalry="low",
    )
    from datetime import datetime

    good = MoatOutput(
        moat_width="wide",
        moat_trend="stable",
        moat_types_present=[],
        evidence_for=[],
        evidence_against=[
            {
                "claim": "x",
                "sources": [
                    {
                        "source_type": "calculated",
                        "source_id": "x",
                        "section": None,
                        "retrieved_at": datetime.now(UTC).isoformat(),
                        "url": None,
                    }
                ],
                "confidence": "low",
            }
        ],
        five_forces_summary=five,
        qualitative_summary="No numeric claims here.",
        confidence=Confidence.MEDIUM,
    )
    # Now scan a prose snippet through the helper (bypassing Pydantic) for broader coverage.
    object.__setattr__(good, "qualitative_summary", "Services grew 37% and 12.5% in two regions.")
    offenses = validate_numeric_claims(good, text_fields=["qualitative_summary"])
    assert any("37%" in o for o in offenses)
    assert any("12.5" in o for o in offenses)
