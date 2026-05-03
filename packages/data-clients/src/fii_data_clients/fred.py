"""FRED client — St. Louis Fed API. Free, 120 req/min.

We use the FRED HTTP API directly (not fredapi) so it fits our BaseHttpClient shape and
shares the same retry/rate-limit/redaction semantics as the other providers.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fii_data_clients.base import BaseHttpClient

FRED_BASE = "https://api.stlouisfed.org"


class FredClient(BaseHttpClient):
    provider = "fred"
    default_rate_per_sec = 2.0  # 120/min with headroom
    default_burst = 10

    def __init__(self, *, api_key: str, rate_per_sec: float | None = None) -> None:
        super().__init__(base_url=FRED_BASE, api_key=api_key, rate_per_sec=rate_per_sec)

    def _apply_auth(
        self, headers: dict[str, str], params: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any]]:
        params.setdefault("api_key", self.api_key or "")
        params.setdefault("file_type", "json")
        return headers, params

    async def get_series(
        self, series_id: str, *, start: date | None = None, end: date | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"series_id": series_id}
        if start:
            params["observation_start"] = start.isoformat()
        if end:
            params["observation_end"] = end.isoformat()
        data = await self.get_json("/fred/series/observations", params=params)
        obs = data.get("observations") if isinstance(data, dict) else None
        return list(obs or [])

    async def get_latest_value(self, series_id: str) -> dict[str, Any] | None:
        data = await self.get_json(
            "/fred/series/observations",
            params={"series_id": series_id, "sort_order": "desc", "limit": 1},
        )
        obs = data.get("observations") if isinstance(data, dict) else None
        return obs[0] if obs else None


# Default macro series ingested by the macro job.
DEFAULT_MACRO_SERIES: tuple[str, ...] = (
    "DGS10",
    "DGS2",
    "DFF",
    "UNRATE",
    "CPIAUCSL",
    "CPILFESL",
    "PAYEMS",
    "GDP",
    "INDPRO",
    "RSXFS",
    "M2SL",
    "T10Y2Y",
    "VIXCLS",
    "DCOILWTICO",
    # GOLDAMGBD228NLBM (London AM fix) was discontinued by FRED — returns HTTP
    # 400 on lookup. Use the still-active PM fix (GOLDPMGBD228NLBM) instead.
    "GOLDPMGBD228NLBM",
    "DEXUSEU",
    "DEXCHUS",
    "USRECD",
    "HOUST",
    "UMCSENT",
    "NFCI",
    "ICSA",
    "PERMIT",
    "TCU",
    "BAMLH0A0HYM2",
)
