"""Financial Modeling Prep client.

Starter tier: 300 requests/minute. Use /stable/ endpoints. Authentication via apikey query param.
"""

from __future__ import annotations

from typing import Any

from fii_data_clients.base import BaseHttpClient

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

    # FMP's "stable" v3-equivalent endpoints.

    async def get_income_statement(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return (
            await self.get_json(
                f"/api/v3/income-statement/{symbol.upper()}",
                params={"period": period, "limit": limit},
            )
            or []
        )

    async def get_balance_sheet(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return (
            await self.get_json(
                f"/api/v3/balance-sheet-statement/{symbol.upper()}",
                params={"period": period, "limit": limit},
            )
            or []
        )

    async def get_cash_flow(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return (
            await self.get_json(
                f"/api/v3/cash-flow-statement/{symbol.upper()}",
                params={"period": period, "limit": limit},
            )
            or []
        )

    async def get_ratios(
        self, symbol: str, *, period: str = "quarter", limit: int = 20
    ) -> list[dict[str, Any]]:
        return (
            await self.get_json(
                f"/api/v3/ratios/{symbol.upper()}",
                params={"period": period, "limit": limit},
            )
            or []
        )

    async def get_analyst_estimates(self, symbol: str) -> list[dict[str, Any]]:
        return await self.get_json(f"/api/v3/analyst-estimates/{symbol.upper()}") or []

    async def get_dcf(self, symbol: str) -> dict[str, Any] | None:
        data = await self.get_json(f"/api/v3/discounted-cash-flow/{symbol.upper()}")
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None

    async def get_profile(self, symbol: str) -> dict[str, Any] | None:
        data = await self.get_json(f"/api/v3/profile/{symbol.upper()}")
        return data[0] if isinstance(data, list) and data else None
