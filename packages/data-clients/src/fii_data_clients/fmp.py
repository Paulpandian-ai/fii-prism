"""Financial Modeling Prep client.

Uses FMP's `/stable/` endpoints (the v3 paths return 403 for accounts created after
2025-08-31). All endpoints accept `symbol` as a query parameter rather than a path
segment. Authentication is the `apikey` query param attached by `_apply_auth`.

Starter tier: 300 requests/minute → default rate-limit 5 req/s, burst 10.
"""

from __future__ import annotations

from typing import Any

import structlog

from fii_data_clients.base import BaseHttpClient

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
        return (
            await self.get_json(
                "/stable/income-statement",
                params={"symbol": symbol.upper(), "period": period, "limit": limit},
            )
            or []
        )

    async def get_balance_sheet(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return (
            await self.get_json(
                "/stable/balance-sheet-statement",
                params={"symbol": symbol.upper(), "period": period, "limit": limit},
            )
            or []
        )

    async def get_cash_flow(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return (
            await self.get_json(
                "/stable/cash-flow-statement",
                params={"symbol": symbol.upper(), "period": period, "limit": limit},
            )
            or []
        )

    async def get_ratios(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return (
            await self.get_json(
                "/stable/ratios",
                params={"symbol": symbol.upper(), "period": period, "limit": limit},
            )
            or []
        )

    # --- Estimates / valuation -----------------------------------------------------------

    async def get_analyst_estimates(
        self, symbol: str, *, period: str = "annual"
    ) -> list[dict[str, Any]]:
        """Stable endpoint requires explicit `period` (`annual` or `quarter`)."""
        return (
            await self.get_json(
                "/stable/analyst-estimates",
                params={"symbol": symbol.upper(), "period": period},
            )
            or []
        )

    async def get_dcf(self, symbol: str) -> dict[str, Any] | None:
        """FMP's stable DCF endpoint is `/stable/discounted-cash-flow-valuation`. Returns
        a one-element list when a DCF is available, or HTTP 200 with `[]` when FMP has
        no precomputed valuation for the ticker. We treat empty as a soft miss: log a
        warning and return None so callers can fall back to their own DCF math."""
        data = await self.get_json(
            "/stable/discounted-cash-flow-valuation",
            params={"symbol": symbol.upper()},
        )
        if isinstance(data, list):
            if not data:
                log.warning("fmp_dcf_unavailable", symbol=symbol.upper())
                return None
            return data[0]
        return data if isinstance(data, dict) else None

    # --- Profile -------------------------------------------------------------------------

    async def get_profile(self, symbol: str) -> dict[str, Any] | None:
        data = await self.get_json("/stable/profile", params={"symbol": symbol.upper()})
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None
