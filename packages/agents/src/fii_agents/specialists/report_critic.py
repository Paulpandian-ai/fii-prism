"""Report critic — two-call workflow comparing an analyst PDF against our specialist cache.

NOT a regular specialist:
  - No entry in `specialist_cache`. Outputs are persisted to `report_critiques` instead.
  - Two LLM calls per critique: claim extraction (PDF input) → critique generation.
  - Total cost cap: $0.90 by default (FII_COST_CAP_REPORT_CRITIC). Per-call sub-caps
    are private constants below.

The extractor consumes a PDF as a `document` content block (Base64PDFSourceParam).
The critic consumes the extracted claims JSON plus whatever specialist outputs
exist in cache for the same symbol — when a specialist is missing, the critic is
told to populate `gaps_in_analysis` accordingly.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from fii_shared import ExtractedReportClaims, ReportCritique
from fii_shared.validation import ReprompTicket, try_parse
from sqlalchemy.orm import sessionmaker

from fii_agents.budget import estimate_cost_usd
from fii_agents.model import MODEL_SONNET, Model
from fii_agents.prompts import EXTRACT_CLAIMS_V1, REPORT_CRITIC_V1
from fii_agents.synthesis_runner import _load_priors_from_cache

log = structlog.get_logger(__name__)


# Sub-call caps. The extractor sees a (potentially large) PDF in the input so
# it gets the bigger budget; the critic's input is bounded JSON.
EXTRACTOR_COST_CAP_USD = 0.50
EXTRACTOR_MAX_TOKENS = 3000
CRITIC_COST_CAP_USD = 0.40
CRITIC_MAX_TOKENS = 4000

# Up to 3 schema-validity retries per call. Mirrors the synthesis attempt cap so
# a flaky JSON emission doesn't cost more than 3x the per-call budget.
MAX_PARSE_ATTEMPTS = 3


@dataclass
class ReportCriticResult:
    status: str  # 'ok' | 'error' | 'extract_invalid' | 'critique_invalid' | 'aborted_cap'
    extracted_claims: dict[str, Any] | None
    critique: dict[str, Any] | None
    cost_usd: float
    tokens_in: int
    tokens_out: int
    duration_ms: int
    model_used: str
    error: str | None = None
    # Specialists not found in cache for this symbol; surfaced so the critique's
    # gaps_in_analysis can name them explicitly.
    missing_specialists: list[str] = field(default_factory=list)
    stale_specialists: list[str] = field(default_factory=list)


# --- Public entry point ------------------------------------------------------------------


async def run_report_critique(
    *,
    factory: sessionmaker,
    symbol: str,
    critique_id: str,
    report_source: str,
    pdf_bytes: bytes,
) -> ReportCriticResult:
    """Run the two-call critique pipeline. Caller persists the result; this
    function does NOT touch the DB beyond reading the specialist cache."""
    sym = symbol.upper()
    started = time.perf_counter()
    model = Model(model_id=MODEL_SONNET, max_tokens=EXTRACTOR_MAX_TOKENS)
    if model.is_fake:
        # In fake mode we still want to exercise the API + persistence path.
        # Build a minimal-shape ExtractedReportClaims + ReportCritique that
        # satisfies pydantic so tests can assert on persistence end-to-end.
        return _fake_result(critique_id, sym, report_source, started)

    # --- Call 1: extract claims ---------------------------------------------------------
    extract_messages = _build_extract_messages(sym, report_source, pdf_bytes)
    extracted, e_tokens_in, e_tokens_out, e_cost, e_err = await _call_with_retry(
        model=model,
        system_prompt=EXTRACT_CLAIMS_V1.text,
        messages=extract_messages,
        output_schema=ExtractedReportClaims,
        cost_cap_usd=EXTRACTOR_COST_CAP_USD,
    )
    if extracted is None:
        return ReportCriticResult(
            status="extract_invalid" if e_err == "schema_invalid" else "aborted_cap",
            extracted_claims=None,
            critique=None,
            cost_usd=e_cost,
            tokens_in=e_tokens_in,
            tokens_out=e_tokens_out,
            duration_ms=int((time.perf_counter() - started) * 1000),
            model_used=model.model_id,
            error=f"claim extraction failed ({e_err})",
        )

    # --- Specialist cache lookup --------------------------------------------------------
    state_priors, missing, stale = _load_priors_from_cache(factory, sym)

    # --- Call 2: produce critique -------------------------------------------------------
    critic_model = Model(model_id=MODEL_SONNET, max_tokens=CRITIC_MAX_TOKENS)
    critic_messages = _build_critic_messages(
        critique_id=critique_id,
        symbol=sym,
        extracted=extracted,
        priors=state_priors,
        missing=missing,
        stale=stale,
    )
    critique, c_tokens_in, c_tokens_out, c_cost, c_err = await _call_with_retry(
        model=critic_model,
        system_prompt=REPORT_CRITIC_V1.text,
        messages=critic_messages,
        output_schema=ReportCritique,
        cost_cap_usd=CRITIC_COST_CAP_USD,
    )

    total_cost = e_cost + c_cost
    total_in = e_tokens_in + c_tokens_in
    total_out = e_tokens_out + c_tokens_out
    duration_ms = int((time.perf_counter() - started) * 1000)

    if critique is None:
        return ReportCriticResult(
            status="critique_invalid" if c_err == "schema_invalid" else "aborted_cap",
            extracted_claims=extracted.model_dump(mode="json"),
            critique=None,
            cost_usd=total_cost,
            tokens_in=total_in,
            tokens_out=total_out,
            duration_ms=duration_ms,
            model_used=critic_model.model_id,
            error=f"critique generation failed ({c_err})",
            missing_specialists=missing,
            stale_specialists=stale,
        )

    # Stamp the running cost into the critique's `cost_usd` field for transparency.
    critique = critique.model_copy(update={"cost_usd": total_cost})

    log.info(
        "report_critique_complete",
        critique_id=critique_id,
        symbol=sym,
        cost_usd=round(total_cost, 6),
        missing=missing,
        stale=stale,
    )
    return ReportCriticResult(
        status="ok",
        extracted_claims=extracted.model_dump(mode="json"),
        critique=critique.model_dump(mode="json"),
        cost_usd=total_cost,
        tokens_in=total_in,
        tokens_out=total_out,
        duration_ms=duration_ms,
        model_used=critic_model.model_id,
        missing_specialists=missing,
        stale_specialists=stale,
    )


# --- Message builders --------------------------------------------------------------------


def _build_extract_messages(
    symbol: str, report_source: str, pdf_bytes: bytes
) -> list[dict[str, Any]]:
    """User turn: PDF as a base64 document block + a short instruction.
    `report_source` is forwarded so the model can populate the field on the schema."""
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        f"Extract the claims from the attached analyst report on {symbol}. "
                        f"The report_source is {report_source!r}. Output an "
                        "ExtractedReportClaims JSON object."
                    ),
                },
            ],
        }
    ]


def _build_critic_messages(
    *,
    critique_id: str,
    symbol: str,
    extracted: ExtractedReportClaims,
    priors: dict[str, Any],
    missing: list[str],
    stale: list[str],
) -> list[dict[str, Any]]:
    """User turn: extracted claims + cached specialist outputs + explicit
    missing/stale markers so the model knows which specialists it can cite vs
    which it must surface in `gaps_in_analysis`.
    """
    parts: list[str] = []
    parts.append(
        f"Critique the following analyst report on {symbol}. The critique_id is "
        f"{critique_id!r}; include it in your output."
    )
    parts.append(
        f"<extracted_report_claims>\n{json.dumps(extracted.model_dump(mode='json'), default=str)}\n"
        "</extracted_report_claims>"
    )
    if priors:
        parts.append(
            "<specialist_cache>\n"
            + json.dumps(priors, default=str)
            + "\n</specialist_cache>"
        )
    else:
        parts.append("<specialist_cache>{}</specialist_cache>")
    if missing or stale:
        parts.append(
            "<unavailable_specialists>\n"
            + json.dumps({"missing": missing, "stale": stale})
            + "\n</unavailable_specialists>\n"
            "When citing gaps_in_analysis, name the specific missing/stale specialist."
        )
    return [{"role": "user", "content": [{"type": "text", "text": "\n\n".join(parts)}]}]


# --- Retry-able single-call helper -------------------------------------------------------


async def _call_with_retry(
    *,
    model: Model,
    system_prompt: str,
    messages: list[dict[str, Any]],
    output_schema: type,
    cost_cap_usd: float,
) -> tuple[Any | None, int, int, float, str | None]:
    """Run one prompt; retry up to MAX_PARSE_ATTEMPTS on schema-validity failure.

    Returns (parsed_pydantic_or_None, tokens_in, tokens_out, cost_usd, error_kind).
    error_kind is None on success, "schema_invalid" if all retries failed parse,
    or "cost_cap" if we'd exceed the per-call cap before the next retry.
    """
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0
    last_text = ""
    last_ticket: ReprompTicket | None = None
    convo: list[dict[str, Any]] = list(messages)

    for _attempt in range(MAX_PARSE_ATTEMPTS):
        if cost_usd >= cost_cap_usd:
            return None, tokens_in, tokens_out, cost_usd, "cost_cap"
        try:
            call = await model.respond(system=system_prompt, messages=convo, tools=None)
        except Exception as exc:
            return None, tokens_in, tokens_out, cost_usd, f"exception:{type(exc).__name__}"

        tokens_in += call.tokens_in
        tokens_out += call.tokens_out
        cost_usd += estimate_cost_usd(call.model, call.tokens_in, call.tokens_out)
        last_text = call.text or last_text

        parsed = try_parse(output_schema, _strip_fence(call.text))
        if isinstance(parsed, ReprompTicket):
            last_ticket = parsed
            convo.append({"role": "assistant", "content": call.text})
            convo.append({"role": "user", "content": parsed.as_prompt()})
            continue
        return parsed, tokens_in, tokens_out, cost_usd, None

    log.warning(
        "report_critic_schema_invalid_after_retries",
        attempts=MAX_PARSE_ATTEMPTS,
        last_errors=(list(last_ticket.errors) if last_ticket else None),
        last_text=(last_text or "")[:300],
    )
    return None, tokens_in, tokens_out, cost_usd, "schema_invalid"


def _strip_fence(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if "\n" in s:
            first, rest = s.split("\n", 1)
            if first.strip().lower() in {"json", "json5"}:
                s = rest
    return s.strip()


# --- Fake-mode shim ----------------------------------------------------------------------


def _fake_result(
    critique_id: str, symbol: str, report_source: str, started: float
) -> ReportCriticResult:
    """Deterministic schema-valid output for tests + local dev without a key."""
    extracted = {
        "symbol": symbol,
        "report_source": report_source,
        "analyst_name": None,
        "publication_date": None,
        "recommendation": None,
        "price_target": None,
        "time_horizon": None,
        "bull_case_summary": "(fake-mode) PDF not parsed; no real LLM call.",
        "bear_case_summary": "(fake-mode) PDF not parsed; no real LLM call.",
        "key_numerical_claims": [],
        "key_qualitative_claims": [],
        "stated_assumptions": [],
        "analyst_disclosures": [],
    }
    critique = {
        "critique_id": critique_id,
        "symbol": symbol,
        "report_source": report_source,
        "analyst_name": None,
        "numerical_accuracy": [],
        "logical_strengths": [],
        "logical_weaknesses": [],
        "unstated_assumptions": [],
        "bias_indicators": [],
        "gaps_in_analysis": [],
        "reliability_rating": "medium",
        "reliability_rationale": (
            "(fake-mode placeholder; set ANTHROPIC_API_KEY to run the real critic)"
        ),
        "one_line_verdict": "Fake-mode placeholder critique.",
        "cost_usd": 0.0,
    }
    return ReportCriticResult(
        status="ok",
        extracted_claims=extracted,
        critique=critique,
        cost_usd=0.0,
        tokens_in=0,
        tokens_out=0,
        duration_ms=int((time.perf_counter() - started) * 1000),
        model_used="fake",
    )
