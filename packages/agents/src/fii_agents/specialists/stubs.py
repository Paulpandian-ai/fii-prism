"""STUB specialists — return schema-valid hardcoded outputs so the full graph walks.

Each class is marked clearly and will be replaced in Section 5 with a real Claude-backed
implementation. The outputs are plausible-for-AAPL placeholders; the point is only to
let the orchestrator, synthesis, and persistence layers be exercised end-to-end.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fii_shared import (
    BullBearDebateOutput,
    CitedClaim,
    CitedNumber,
    CompParable,
    DfastScenario,
    FiveForces,
    InsiderFlowOutput,
    InsiderMove,
    MacroOutput,
    MoatOutput,
    NewsSentimentOutput,
    RiskOutput,
    SourceRef,
    SourceType,
    TechnicalOutput,
    ValuationOutput,
)

from fii_agents.model import Model
from fii_agents.specialists.base import SpecialistContext, SpecialistResult

_NOW = lambda: datetime.now(UTC)  # noqa: E731


def _calc_source(reason: str) -> SourceRef:
    return SourceRef(
        source_type=SourceType.CALCULATED,
        source_id=f"stub/{reason}",
        section=None,
        retrieved_at=_NOW(),
        url=None,
    )


def _price_source(symbol: str) -> SourceRef:
    return SourceRef(
        source_type=SourceType.POLYGON_PRICE,
        source_id=f"polygon/aggs/{symbol}/latest",
        section=None,
        retrieved_at=_NOW(),
        url=None,
    )


def _fred_source(series_id: str) -> SourceRef:
    return SourceRef(
        source_type=SourceType.FRED_SERIES,
        source_id=series_id,
        section=None,
        retrieved_at=_NOW(),
        url=None,
    )


def _news_source(ref: str) -> SourceRef:
    return SourceRef(
        source_type=SourceType.FINNHUB_NEWS,
        source_id=f"finnhub/news/{ref}",
        section=None,
        retrieved_at=_NOW(),
        url=None,
    )


def _sec_source(accession: str) -> SourceRef:
    return SourceRef(
        source_type=SourceType.SEC_FILING,
        source_id=accession,
        section=None,
        retrieved_at=_NOW(),
        url=None,
    )


def _cited(value: float, unit: str, source: SourceRef, *, as_of: date | None = None) -> CitedNumber:
    return CitedNumber(value=value, unit=unit, as_of=as_of or date.today(), source=source)


def _cited_claim(text: str, source: SourceRef) -> CitedClaim:
    return CitedClaim(claim=text, sources=[source], confidence="medium")


# --- STUB — implement in section 5 -------------------------------------------------------


class ValuationStub:
    name = "valuation"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        src = _calc_source("dcf")
        output = ValuationOutput(
            dcf_intrinsic_value_bear=_cited(150.0, "USD", src),
            dcf_intrinsic_value_base=_cited(195.0, "USD", src),
            dcf_intrinsic_value_bull=_cited(240.0, "USD", src),
            current_price=_cited(210.0, "USD", _price_source(ctx.symbol)),
            margin_of_safety_pct=_cited(-0.077, "ratio", src),
            wacc_used=_cited(0.085, "ratio", src),
            terminal_growth_used=_cited(0.025, "ratio", src),
            revenue_growth_assumption=_cited(0.06, "ratio", src),
            comparable_multiples=[
                CompParable(peer_symbol="MSFT", ev_ebitda=22.0, pe_ratio=33.0),
                CompParable(peer_symbol="GOOGL", ev_ebitda=14.0, pe_ratio=22.0),
            ],
            reverse_dcf_implied_growth=_cited(0.07, "ratio", src),
            sensitivity_table={"wacc_plus_1pct": 178.0, "terminal_plus_1pct": 208.0},
            qualitative_summary=(
                "STUB valuation output — implement in Section 5. Base case sits modestly "
                "below current price; sensitivity to the discount rate is meaningful."
            ),
            confidence="medium",
        )
        return SpecialistResult(output=output)


class MoatStub:
    name = "moat"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        src = _sec_source("stub-latest-10k")
        output = MoatOutput(
            moat_width="wide",
            moat_trend="stable",
            moat_types_present=["brand", "switching_costs", "intangible_assets"],
            evidence_for=[_cited_claim("Durable ecosystem moat", src)],
            evidence_against=[_cited_claim("Regulatory pressure on platform economics", src)],
            five_forces_summary=FiveForces(
                threat_new_entrants="low",
                bargaining_buyers="medium",
                bargaining_suppliers="low",
                threat_substitutes="medium",
                competitive_rivalry="high",
            ),
            qualitative_summary=(
                "STUB moat output — implement in Section 5. Brand and switching costs "
                "combine into a wide moat; regulatory rather than competitive is the main risk."
            ),
            confidence="medium",
        )
        return SpecialistResult(output=output)


class MacroStub:
    name = "macro"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        output = MacroOutput(
            regime="late_cycle",
            regime_evidence=[_cited_claim("Unemployment off the trough", _fred_source("UNRATE"))],
            rates_trajectory="easing",
            stock_sector_macro_sensitivity={"rates_10y": -0.3, "vix": -0.5},
            top_risks=[_cited_claim("Recession would compress multiples", _fred_source("USRECD"))],
            top_tailwinds=[],
            qualitative_summary=(
                "STUB macro output — implement in Section 5. Late-cycle signals dominate; "
                "policy easing could extend the cycle."
            ),
            confidence="medium",
        )
        return SpecialistResult(output=output)


class TechnicalStub:
    name = "technical"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        psrc = _price_source(ctx.symbol)
        output = TechnicalOutput(
            trend_short="up",
            trend_medium="sideways",
            trend_long="up",
            rsi_14=58.2,
            macd_signal="no_signal",
            adx=22.0,
            realized_vol_30d=0.21,
            atr_14=4.3,
            support_levels=[198.0, 188.0],
            resistance_levels=[218.0, 230.0],
            suggested_entry=_cited(205.0, "USD", psrc),
            suggested_stop=_cited(188.0, "USD", psrc),
            suggested_target=_cited(230.0, "USD", psrc),
            regime="mean_reverting",
            signal="neutral",
            signal_strength="moderate",
            qualitative_summary=(
                "STUB technical output — implement in Section 5. Price is consolidating "
                "between well-defined support and resistance; no trend edge here."
            ),
            confidence="medium",
        )
        return SpecialistResult(output=output)


class NewsSentimentStub:
    name = "news_sentiment"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        output = NewsSentimentOutput(
            net_sentiment=0.12,
            articles_analyzed=47,
            top_positive_themes=[_cited_claim("Services growth narrative", _news_source("svc1"))],
            top_negative_themes=[_cited_claim("China softness", _news_source("cn1"))],
            anomaly_flags=[],
            earnings_guidance_changes=[],
            qualitative_summary=(
                "STUB news sentiment output — implement in Section 5. Mildly positive net "
                "sentiment offset by China narratives."
            ),
            confidence="medium",
        )
        return SpecialistResult(output=output)


class InsiderFlowStub:
    name = "insider_flow"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        src = _sec_source("form4/stub/cluster")
        output = InsiderFlowOutput(
            net_insider_dollars_90d=_cited(-12_500_000.0, "USD", src),
            cluster_buying=False,
            cluster_selling=True,
            top_insider_moves=[
                InsiderMove(
                    name="Insider A",
                    role="Officer",
                    action="sell",
                    shares=100_000.0,
                    dollar_value=21_000_000.0,
                    date=date.today(),
                )
            ],
            institutional_net_change_qoq=_cited(0.012, "ratio", src),
            activist_presence=[],
            qualitative_summary=(
                "STUB insider output — implement in Section 5. Programmatic 10b5-1 sells "
                "dominate; institutional ownership edged up modestly."
            ),
            confidence="medium",
        )
        return SpecialistResult(output=output)


class RiskStub:
    """Used for both `risk_preliminary` and `risk_final`; identical stub output."""

    name = "risk"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        scenarios = {
            "pullback": DfastScenario(
                name="pullback",
                assumed_move_pct=-0.15,
                position_value_start_usd=10_000,
                position_value_after_usd=8_500,
                drawdown_usd=-1_500,
                notes=None,
            ),
            "recession": DfastScenario(
                name="recession",
                assumed_move_pct=-0.30,
                position_value_start_usd=10_000,
                position_value_after_usd=7_000,
                drawdown_usd=-3_000,
                notes="-30% base case",
            ),
            "severe": DfastScenario(
                name="severe",
                assumed_move_pct=-0.50,
                position_value_start_usd=10_000,
                position_value_after_usd=5_000,
                drawdown_usd=-5_000,
                notes=None,
            ),
            "sector_shock": DfastScenario(
                name="sector_shock",
                assumed_move_pct=-0.20,
                position_value_start_usd=10_000,
                position_value_after_usd=8_000,
                drawdown_usd=-2_000,
                notes=None,
            ),
            "bull_rally": DfastScenario(
                name="bull_rally",
                assumed_move_pct=0.25,
                position_value_start_usd=10_000,
                position_value_after_usd=12_500,
                drawdown_usd=2_500,
                notes=None,
            ),
        }
        output = RiskOutput(
            position_size_rec_pct=3.5,
            hard_stop_level=_cited(185.0, "USD", _price_source(ctx.symbol)),
            max_drawdown_historical=_cited(-0.34, "ratio", _price_source(ctx.symbol)),
            correlation_to_portfolio=0.42,
            liquidity_adequate=True,
            concentration_warnings=[],
            dfast_scenarios=scenarios,
            go_no_go="approve_with_conditions",
            conditions=["Size no larger than 3.5 percent of portfolio"],
            qualitative_summary=(
                "STUB risk output — implement in Section 5. Liquidity ample; modest entry "
                "with a stop below near-term support."
            ),
            confidence="medium",
        )
        return SpecialistResult(output=output)


class BullStub:
    name = "bull"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        src = _sec_source("stub-10k")
        output = BullBearDebateOutput(
            case=(
                f"{ctx.symbol} compounds at a high rate of return on capital with a durable "
                "ecosystem moat. Services-led mix shift remains in early innings. Capital "
                "allocation is disciplined. The current multiple looks fair-to-cheap relative "
                "to implied growth in a reverse DCF."
            ),
            strongest_evidence=[_cited_claim("High return on capital", src)],
            weakest_evidence=[],
            what_would_change_my_mind=(
                "A sustained miss on services growth for two consecutive quarters, or a "
                "regulatory outcome that materially compresses platform take-rates."
            ),
            confidence="high",
        )
        return SpecialistResult(output=output)


class BearStub:
    name = "bear"

    async def run(self, ctx: SpecialistContext, model: Model) -> SpecialistResult:
        src = _sec_source("stub-10k")
        output = BullBearDebateOutput(
            case=(
                f"Headwinds for {ctx.symbol} are accumulating: regulatory pressure, peak "
                "hardware saturation, and a services mix dependent on take-rates the courts "
                "may force down. The premium multiple offers little margin of safety."
            ),
            strongest_evidence=[_cited_claim("Regulatory threat to take-rates", src)],
            weakest_evidence=[],
            what_would_change_my_mind=(
                "A definitive regulatory settlement that preserves economics, paired with "
                "services re-acceleration."
            ),
            confidence="medium",
        )
        return SpecialistResult(output=output)
