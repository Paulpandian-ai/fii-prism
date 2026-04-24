"""Cross-cutting validators and the rejection-sampling helper.

The numeric-claim validator is the teeth behind the schema contract: every number
that appears in prose (qualitative_summary fields, case text, thesis) must be either
  (a) wrapped inline as [value:source_id] markdown, OR
  (b) match a CitedNumber.value declared on the same model.

The rejection-sampling helper wraps Pydantic's ValidationError into a human-readable
message the orchestrator can feed back to Claude on re-prompt.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from fii_shared.primitives import CitedNumber

_T = TypeVar("_T", bound=BaseModel)

# --- Numeric-claim validator --------------------------------------------------------------

# Matches numbers we consider "factual claim-like":
#   - Has a unit suffix ($, %, bps, M/B/K/T, " million", " billion", " trillion")
#   - Or contains a decimal point and is NOT a 4-digit year
#
# Plain small integers ("5 specialists", "3 risks") are NOT matched — they're not claims.
_NUMBER_PAT = re.compile(
    r"""
    (?P<num>
        \$?\d{1,3}(?:,\d{3})*(?:\.\d+)?        # 1,234.56 or $1,234.56
        | \$?\d+(?:\.\d+)?                     # 1234 or $1234 or 12.34
    )
    \s*
    (?P<unit>
        %                                      # percent
        | \s*bps\b                             # basis points
        | \s*[MBKT](?![A-Za-z0-9])             # 10M / 1.2B / 500K / 3T (not followed by more)
        | \s*(?:million|billion|trillion)\b    # 10 million
        | \s*pp\b                              # percentage points
    )?
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Matches bracketed citations like [10.5%:ratios.grossProfitMargin] or [$10B:fmp-AAPL-income].
_BRACKET_PAT = re.compile(r"\[[^\]]*\d[^\]]*:[^\]]+\]")

# Years get a pass: a 4-digit integer 1900-2100 standing alone in prose is not a claim.
_YEAR_PAT = re.compile(r"\b(?:19|20)\d{2}\b")


def _strip_bracketed(text: str) -> str:
    return _BRACKET_PAT.sub(" ", text)


def _strip_years(text: str) -> str:
    return _YEAR_PAT.sub(" ", text)


def _is_factual_number(match: re.Match[str]) -> bool:
    """A hit counts as a factual claim if it has a unit OR a decimal point."""
    num = match.group("num").replace(",", "").lstrip("$")
    unit = (match.group("unit") or "").strip()
    if unit:
        return True
    return "." in num


def _cited_number_strings(model: BaseModel) -> set[str]:
    """Return every CitedNumber.value on the model, as a stringified set (multiple formats)."""
    out: set[str] = set()
    for _, field_value in _walk_values(model):
        if isinstance(field_value, CitedNumber):
            v = field_value.value
            out.add(str(v))
            if v == int(v):
                out.add(str(int(v)))
            out.add(f"{v:.2f}")
    return out


def _walk_values(obj: Any) -> list[tuple[str, Any]]:
    """Flatten a pydantic model's values recursively."""
    if isinstance(obj, BaseModel):
        items: list[tuple[str, Any]] = []
        for name, val in obj.__dict__.items():
            items.append((name, val))
            items.extend(_walk_values(val))
        return items
    if isinstance(obj, (list, tuple)):
        out: list[tuple[str, Any]] = []
        for x in obj:
            out.extend(_walk_values(x))
        return out
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out.extend(_walk_values(v))
        return out
    return []


def validate_numeric_claims(model: BaseModel, *, text_fields: list[str]) -> list[str]:
    """Return a list of un-cited numeric claims found in the named prose fields.

    Empty list == pass. Non-empty == rejection: each string is a token that needs
    either a [value:source_id] wrapper or a CitedNumber match.
    """
    cited = _cited_number_strings(model)
    offenses: list[str] = []

    for field in text_fields:
        raw = getattr(model, field, None)
        if not isinstance(raw, str) or not raw:
            continue
        scan = _strip_bracketed(raw)
        scan = _strip_years(scan)
        for m in _NUMBER_PAT.finditer(scan):
            if not _is_factual_number(m):
                continue
            token = m.group(0).strip()
            num = m.group("num").replace(",", "").lstrip("$")
            if num in cited:
                continue
            offenses.append(token)

    return offenses


class NumericClaimsMixin(BaseModel):
    """Mixin that applies validate_numeric_claims to the fields declared in
    `__numeric_claim_text_fields__`. Subclasses just set the class attribute.

    Using a classmethod validator lets us introspect self after parsing.
    """

    __numeric_claim_text_fields__: tuple[str, ...] = ()

    def model_post_init(self, __context: Any, /) -> None:
        fields = list(type(self).__numeric_claim_text_fields__)
        if not fields:
            return
        offenses = validate_numeric_claims(self, text_fields=fields)
        if offenses:
            raise ValueError(
                "Uncited numeric claims in prose: "
                + ", ".join(sorted(set(offenses)))
                + ". Wrap each as [value:source_id] or declare a matching CitedNumber."
            )


# --- Rejection-sampling helper ------------------------------------------------------------


class ReprompTicket(BaseModel):
    """Structured payload for re-prompting Claude after a schema failure.

    The orchestrator feeds `.as_prompt()` back into the next call as a correction turn.
    """

    model_config = ConfigDict(frozen=True)

    model_name: str
    errors: list[str]

    def as_prompt(self) -> str:
        lines = [
            f"Your previous output failed schema validation for `{self.model_name}`:",
            *(f"  - {e}" for e in self.errors),
            "",
            "Return ONLY valid JSON that matches the schema. Do not apologize or explain.",
        ]
        return "\n".join(lines)


def try_parse(model: type[_T], payload: dict | str) -> _T | ReprompTicket:
    """Attempt to parse `payload` into `model`. On failure, return a ReprompTicket with a
    tidy error list suitable for feeding back to Claude. On success, return the model.
    """
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            return ReprompTicket(
                model_name=model.__name__, errors=[f"payload was not valid JSON: {exc.msg}"]
            )
    else:
        data = payload

    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errs: list[str] = []
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            errs.append(f"{loc}: {e['msg']}")
        return ReprompTicket(model_name=model.__name__, errors=errs)
