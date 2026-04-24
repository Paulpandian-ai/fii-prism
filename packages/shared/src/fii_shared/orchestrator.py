"""Top-level synthesis from the Master Orchestrator. This is the JSON a deep-dive run
writes to S3 and references from the analyses table."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fii_shared.primitives import CitedClaim, Confidence
from fii_shared.specialists import DfastScenario
from fii_shared.validation import NumericClaimsMixin

Recommendation = Literal["strong_buy", "buy", "hold", "trim", "sell"]
TimeHorizon = Literal["short (days-weeks)", "medium (months)", "long (years)"]

EDUCATIONAL_DISCLAIMER = "For educational purposes only. Not investment advice."


class CostSummary(BaseModel):
    total_usd: float = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    model_calls: int = Field(ge=0)


class OrchestratorFinalOutput(NumericClaimsMixin):
    """The final synthesis a deep-dive produces. Archived in S3, indexed in Postgres."""

    model_config = ConfigDict(str_strip_whitespace=True)
    __numeric_claim_text_fields__ = ("thesis", "what_i_would_buy")

    symbol: str = Field(min_length=1, max_length=10)
    analysis_id: UUID

    recommendation: Recommendation
    confidence: Confidence
    fii_score: float = Field(ge=0, le=10)

    thesis: str = Field(max_length=2400, description="The 'why', ≤ 300 words")
    what_i_would_buy: str | None = Field(
        default=None, description="Price / size / stop IF buy; required when recommendation is buy"
    )

    # REQUIRED — at least 3 ways the thesis could be wrong. This is the adversarial frame
    # that keeps the synthesis honest.
    what_could_make_me_wrong: list[CitedClaim] = Field(min_length=3)

    time_horizon: TimeHorizon
    specialist_summaries: dict[str, str] = Field(default_factory=dict)

    # Reuse DfastScenario from the Risk specialist so stress outcomes stay typed.
    stress_outcomes: dict[str, DfastScenario] = Field(default_factory=dict)

    cost_summary: CostSummary
    disclaimer: str = Field(default=EDUCATIONAL_DISCLAIMER)

    # Extra invariant: if the recommendation is buy/strong_buy, what_i_would_buy must be set.
    def model_post_init(self, __context, /) -> None:
        # Run the inherited numeric-claim scan first.
        super().model_post_init(__context)

        if self.recommendation in ("buy", "strong_buy") and not self.what_i_would_buy:
            raise ValueError(
                "recommendation is buy/strong_buy but what_i_would_buy is empty — "
                "agent must provide concrete price / size / stop."
            )
        if self.disclaimer.strip() != EDUCATIONAL_DISCLAIMER:
            raise ValueError(
                "disclaimer must be exactly: 'For educational purposes only. Not investment advice.'"
            )
