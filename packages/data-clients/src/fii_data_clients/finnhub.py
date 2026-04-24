"""Finnhub client — free tier: 60 calls/min.

Kept from v1 for news + earnings calendar continuity.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fii_data_clients.base import BaseHttpClient

FINNHUB_BASE = "https://finnhub.io/api/v1"


class FinnhubClient(BaseHttpClient):
    provider = "finnhub"
    default_rate_per_sec = 1.0  # 60/min, headroom for Retry-After
    default_burst = 5

    def __init__(self, *, api_key: str, rate_per_sec: float | None = None) -> None:
        super().__init__(base_url=FINNHUB_BASE, api_key=api_key, rate_per_sec=rate_per_sec)

    def _apply_auth(
        self, headers: dict[str, str], params: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        headers.setdefault("X-Finnhub-Token", self.api_key or "")
        return headers, params

    async def get_company_news(
        self, symbol: str, *, since: date, until: date | None = None
    ) -> list[dict[str, Any]]:
        until = until or date.today()
        data = await self.get_json(
            "/company-news",
            params={"symbol": symbol.upper(), "from": since.isoformat(), "to": until.isoformat()},
        )
        return list(data or [])

    async def get_earnings_calendar(
        self, *, symbol: str | None = None, start: date | None = None, end: date | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        if start:
            params["from"] = start.isoformat()
        if end:
            params["to"] = end.isoformat()
        data = await self.get_json("/calendar/earnings", params=params)
        return list(data.get("earningsCalendar") or []) if isinstance(data, dict) else []

    async def get_peers(self, symbol: str) -> list[str]:
        data = await self.get_json("/stock/peers", params={"symbol": symbol.upper()})
        return list(data or [])
