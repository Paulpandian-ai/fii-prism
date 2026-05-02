"""Financial Modeling Prep client.

Uses FMP's `/stable/` endpoints (the v3 paths return 403 for accounts created after
2025-08-31). All endpoints accept `symbol` as a query parameter rather than a path
segment. Authentication is the `apikey` query param attached by `_apply_auth`.

Starter tier: 300 requests/minute → default rate-limit 5 req/s, burst 10.

Plan-gating (FMP returns HTTP 402 Payment Required when an endpoint or parameter
combination requires a higher tier):
- `/stable/ratios?period=quarter` is **gated** on Starter; annual ratios are allowed.
  We auto-fall-back to annual on 402 and log `fmp_ratios_quarter_blocked_falling_back_to_annual`.
- `/stable/discounted-cash-flow-valuation` is **gated** on Starter. Returns None +
  `fmp_dcf_not_in_plan` warning. Specialists fall back to in-process DCF math.
- `/stable/analyst-estimates` is **gated** on Starter. Returns `[]` +
  `fmp_analyst_estimates_not_in_plan` warning.

See `docs/RUNBOOK.md` "FMP Starter plan limits" for the full list and upgrade path.
"""

from __future__ import annotations

from typing import Any

import structlog

from fii_data_clients.base import BaseHttpClient, ProviderError

log = structlog.get_logger(__name__)

FMP_BASE = "https://financialmodelingprep.com"


class FMPClient(BaseHttpClient):
    provider = "fmp"
    default_rate_per_sec = 5.0  # 300/min
    default_burst = 10

    def __init__(self, *, api_key: str, rate_per_sec: float | None = None) -> None:
        super().__init__(base_url=FMP_BASE, api_key=api_key, rate_per_sec=rate_per_sec)

    def _apply_auth(
        self, headers: dict[str, str], params: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        params.setdefault("apikey", self.api_key or "")
        return headers, params

    # --- Statements ----------------------------------------------------------------------

    async def get_income_statement(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self._get_list_or_402(
            "/stable/income-statement",
            params={"symbol": symbol.upper(), "period": period, "limit": limit},
            event_name="fmp_income_statement_not_in_plan",
        )

    async def get_balance_sheet(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self._get_list_or_402(
            "/stable/balance-sheet-statement",
            params={"symbol": symbol.upper(), "period": period, "limit": limit},
            event_name="fmp_balance_sheet_not_in_plan",
        )

    async def get_cash_flow(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self._get_list_or_402(
            "/stable/cash-flow-statement",
            params={"symbol": symbol.upper(), "period": period, "limit": limit},
            event_name="fmp_cash_flow_not_in_plan",
        )

    async def get_ratios(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        """FMP gates `period=quarter` on the Starter plan. On HTTP 402 with quarter,
        we automatically retry with `period="annual"` (which Starter allows) and log
        the fallback. If even annual returns 402 we log + return `[]` so the seed
        keeps moving."""
        sym = symbol.upper()
        resp = await self.request(
            "GET",
            "/stable/ratios",
            params={"symbol": sym, "period": period, "limit": limit},
            allow_status=(402,),
        )
        if resp.status_code == 402:
            if period == "quarter":
                log.warning(
                    "fmp_ratios_quarter_blocked_falling_back_to_annual",
                    symbol=sym,
                )
                return await self.get_ratios(symbol, period="annual", limit=limit)
            log.warning("fmp_ratios_blocked_skipping", symbol=sym, period=period)
            return []
        return resp.json() or []

    # --- Estimates / valuation -----------------------------------------------------------

    async def get_analyst_estimates(
        self, symbol: str, *, period: str = "annual"
    ) -> list[dict[str, Any]]:
        """Stable endpoint requires explicit `period` (`annual` or `quarter`). Gated on
        the Starter plan; HTTP 402 → log + return `[]`."""
        sym = symbol.upper()
        resp = await self.request(
            "GET",
            "/stable/analyst-estimates",
            params={"symbol": sym, "period": period},
            allow_status=(402,),
        )
        if resp.status_code == 402:
            log.warning("fmp_analyst_estimates_not_in_plan", symbol=sym, period=period)
            return []
        return resp.json() or []

    async def get_dcf(self, symbol: str) -> dict[str, Any] | None:
        """FMP's stable DCF endpoint is `/stable/discounted-cash-flow-valuation`.

        Three soft-miss paths, all return None (so callers can run their own DCF):
          - HTTP 402 (Starter-plan-gated): log fmp_dcf_not_in_plan
          - HTTP 404 (no precomputed DCF for this ticker): log fmp_dcf_unavailable
          - HTTP 200 with body `[]` (same as 404, different surface): same warning

        Other 4xx (401/403) and 5xx still propagate as ProviderError / UpstreamError
        because they indicate misconfiguration or a real outage.
        """
        sym = symbol.upper()
        resp = await self.request(
            "GET",
            "/stable/discounted-cash-flow-valuation",
            params={"symbol": sym},
            allow_status=(402, 404),
        )
        if resp.status_code == 402:
            log.warning("fmp_dcf_not_in_plan", symbol=sym)
            return None
        if resp.status_code == 404:
            log.warning("fmp_dcf_unavailable", symbol=sym, reason="http_404")
            return None
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"fmp dcf returned malformed JSON: {resp.text[:200]}") from exc
        if isinstance(data, list):
            if not data:
                log.warning("fmp_dcf_unavailable", symbol=sym, reason="empty_200")
                return None
            return data[0]
        return data if isinstance(data, dict) else None

    # --- Profile -------------------------------------------------------------------------

    async def get_profile(self, symbol: str) -> dict[str, Any] | None:
        sym = symbol.upper()
        resp = await self.request(
            "GET",
            "/stable/profile",
            params={"symbol": sym},
            allow_status=(402,),
        )
        if resp.status_code == 402:
            log.warning("fmp_profile_not_in_plan", symbol=sym)
            return None
        data = resp.json()
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None

    # --- Internal: list-shaped endpoints with 402 soft-miss ------------------------------

    async def _get_list_or_402(
        self, path: str, *, params: dict[str, Any], event_name: str
    ) -> list[dict[str, Any]]:
        """GET that returns `[]` and logs `event_name` on HTTP 402, otherwise normal.

        Used for endpoints where the empty-list path is the right soft-miss shape and
        where we don't need a fallback parameter (the income/balance/cash-flow trio
        all behave this way today on Starter, but staying defensive against FMP
        re-tiering them later)."""
        resp = await self.request("GET", path, params=params, allow_status=(402,))
        if resp.status_code == 402:
            log.warning(
                event_name,
                **{k: v for k, v in params.items() if k != "apikey"},
            )
            return []
        return resp.json() or []
