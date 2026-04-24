"""Polygon.io client.

Starter tier: 5 calls/minute. We set rate_per_sec conservatively at 0.08 (= 4.8/min) and
rely on the token bucket to smooth bursts. Paid tier can bump rate_per_sec at init.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from fii_data_clients.base import BaseHttpClient

POLYGON_BASE = "https://api.polygon.io"


class PolygonClient(BaseHttpClient):
    provider = "polygon"
    default_rate_per_sec = 0.08  # 5/min Starter tier
    default_burst = 5

    def __init__(self, *, api_key: str, rate_per_sec: float | None = None) -> None:
        super().__init__(base_url=POLYGON_BASE, api_key=api_key, rate_per_sec=rate_per_sec)

    def _apply_auth(
        self, headers: dict[str, str], params: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        params.setdefault("apiKey", self.api_key or "")
        return headers, params

    async def get_daily_bars(
        self, symbol: str, start: date, end: date, *, adjusted: bool = True
    ) -> list[dict[str, Any]]:
        """Aggregates/v2: daily OHLCV bars between inclusive dates."""
        path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start.isoformat()}/{end.isoformat()}"
        data = await self.get_json(
            path,
            params={"adjusted": "true" if adjusted else "false", "sort": "asc", "limit": 50000},
        )
        return list(data.get("results") or [])

    async def get_intraday_bars(
        self, symbol: str, start: date, end: date, *, multiplier: int = 1, timespan: str = "minute"
    ) -> list[dict[str, Any]]:
        path = (
            f"/v2/aggs/ticker/{symbol.upper()}/range/{multiplier}/{timespan}"
            f"/{start.isoformat()}/{end.isoformat()}"
        )
        data = await self.get_json(path, params={"adjusted": "true", "sort": "asc", "limit": 50000})
        return list(data.get("results") or [])

    async def get_snapshot(self, symbol: str) -> dict[str, Any] | None:
        path = f"/v3/snapshot/stocks/tickers/{symbol.upper()}"
        data = await self.get_json(path)
        return data.get("ticker") if isinstance(data, Mapping) else None
