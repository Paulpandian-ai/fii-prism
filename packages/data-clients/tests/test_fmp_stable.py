"""Unit tests for the FMP /stable/ endpoint migration.

Mocks httpx so we exercise the URL-shape + response-parsing logic without hitting the
real FMP API. The base client's CircuitBreaker + token-bucket + retry layers are
covered separately in test_resilience.py and don't need re-mocking here — we just
want to verify the right /stable/ paths get hit and the parsers tolerate the shapes
FMP actually returns (including empty-list DCF).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fii_data_clients.fmp import FMPClient


def _mock_transport(handler):
    """Build an httpx MockTransport that delegates each request to `handler`."""
    return httpx.MockTransport(handler)


async def _client_with_mock(handler) -> FMPClient:
    """Construct an FMPClient whose underlying httpx.AsyncClient is wired to a mock
    transport. We bypass `__aenter__` and inject directly so each test owns its
    own assertions on URL shape."""
    c = FMPClient(api_key="fake-test-key")
    c._client = httpx.AsyncClient(
        base_url=c.base_url,
        timeout=c._timeout,
        transport=_mock_transport(handler),
    )
    return c


# --- get_profile ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_hits_stable_with_symbol_query() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {
                    "symbol": "AAPL",
                    "companyName": "Apple Inc.",
                    "sector": "Technology",
                    "industry": "Consumer Electronics",
                    "mktCap": 3_500_000_000_000,
                    "cik": "0000320193",
                }
            ],
        )

    c = await _client_with_mock(handler)
    try:
        profile = await c.get_profile("aapl")
    finally:
        await c._client.aclose()

    assert "/stable/profile" in captured["url"]
    assert captured["params"]["symbol"] == "AAPL"
    assert captured["params"]["apikey"] == "fake-test-key"
    assert profile is not None
    assert profile["companyName"] == "Apple Inc."
    assert profile["sector"] == "Technology"


# --- get_income_statement (period + limit propagated) -------------------------------------


@pytest.mark.asyncio
async def test_income_statement_propagates_period_and_limit() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        captured["path"] = request.url.path
        return httpx.Response(
            200,
            json=[
                {
                    "date": "2025-09-30",
                    "calendarYear": "2025",
                    "period": "Q4",
                    "reportedCurrency": "USD",
                    "revenue": 100_000_000,
                    "grossProfit": 45_000_000,
                    "netIncome": 25_000_000,
                    "epsdiluted": 1.50,
                    "weightedAverageShsOutDil": 16_000_000_000,
                }
            ],
        )

    c = await _client_with_mock(handler)
    try:
        rows = await c.get_income_statement("AAPL", period="quarter", limit=4)
    finally:
        await c._client.aclose()

    assert captured["path"] == "/stable/income-statement"
    assert captured["params"]["symbol"] == "AAPL"
    assert captured["params"]["period"] == "quarter"
    assert captured["params"]["limit"] == "4"
    assert len(rows) == 1
    # Field shape used by the fundamentals ingest parser must still be present.
    row = rows[0]
    assert row["revenue"] == 100_000_000
    assert row["epsdiluted"] == 1.50
    assert row["calendarYear"] == "2025"
    assert row["period"] == "Q4"


# --- get_dcf soft-miss paths --------------------------------------------------------------


def _patch_warnings(monkeypatch) -> list[tuple[str, dict]]:
    """Capture every fmp_mod.log.warning call so tests can assert on event + kwargs."""
    captured: list[tuple[str, dict]] = []
    from fii_data_clients import fmp as fmp_mod

    original = fmp_mod.log.warning

    def fake_warning(event: str, **kwargs):
        captured.append((event, kwargs))
        return original(event, **kwargs)

    monkeypatch.setattr(fmp_mod.log, "warning", fake_warning)
    return captured


@pytest.mark.asyncio
async def test_dcf_empty_200_returns_none_and_logs_warning(monkeypatch) -> None:
    """FMP returns HTTP 200 with `[]` for some tickers when a DCF is unavailable.
    Must not raise; must return None; must emit fmp_dcf_unavailable with reason=empty_200."""
    captured = _patch_warnings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/stable/discounted-cash-flow-valuation"
        assert dict(request.url.params)["symbol"] == "AAPL"
        return httpx.Response(200, json=[])

    c = await _client_with_mock(handler)
    try:
        result = await c.get_dcf("AAPL")
    finally:
        await c._client.aclose()

    assert result is None
    assert any(
        event == "fmp_dcf_unavailable" and kwargs.get("reason") == "empty_200"
        for event, kwargs in captured
    ), f"expected fmp_dcf_unavailable(reason=empty_200); got {captured}"
    assert any(kwargs.get("symbol") == "AAPL" for _, kwargs in captured)


@pytest.mark.asyncio
async def test_dcf_404_returns_none_and_logs_warning(monkeypatch) -> None:
    """FMP also returns HTTP 404 (sometimes with body `[]`, sometimes empty body) for
    tickers without a precomputed DCF. Must be treated identically to the empty-200
    case: no raise, return None, fmp_dcf_unavailable with reason=http_404."""
    captured = _patch_warnings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/stable/discounted-cash-flow-valuation"
        assert dict(request.url.params)["symbol"] == "AAPL"
        return httpx.Response(404, content=b"[]", headers={"content-type": "application/json"})

    c = await _client_with_mock(handler)
    try:
        result = await c.get_dcf("AAPL")
    finally:
        await c._client.aclose()

    assert result is None
    assert any(
        event == "fmp_dcf_unavailable" and kwargs.get("reason") == "http_404"
        for event, kwargs in captured
    ), f"expected fmp_dcf_unavailable(reason=http_404); got {captured}"


@pytest.mark.asyncio
async def test_dcf_other_4xx_still_raises() -> None:
    """403 (auth issue) is a real error. Must propagate as ProviderError so operators
    notice — it is NOT a soft miss."""
    from fii_data_clients.base import ProviderError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="not authorized")

    c = await _client_with_mock(handler)
    try:
        with pytest.raises(ProviderError):
            await c.get_dcf("AAPL")
    finally:
        await c._client.aclose()


@pytest.mark.asyncio
async def test_dcf_500_still_raises() -> None:
    """5xx is a real upstream error. Must propagate (and trip the breaker via the base
    client's retry path)."""
    from fii_data_clients.base import ProviderError, UpstreamError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    c = await _client_with_mock(handler)
    try:
        with pytest.raises((UpstreamError, ProviderError)):
            await c.get_dcf("AAPL")
    finally:
        await c._client.aclose()


@pytest.mark.asyncio
async def test_dcf_populated_list_returns_first_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"symbol": "AAPL", "dcf": 195.50, "Stock Price": 175.00, "date": "2025-10-01"}],
        )

    c = await _client_with_mock(handler)
    try:
        result = await c.get_dcf("AAPL")
    finally:
        await c._client.aclose()

    assert result is not None
    assert result["symbol"] == "AAPL"
    assert result["dcf"] == 195.50


# --- get_analyst_estimates default period -------------------------------------------------


@pytest.mark.asyncio
async def test_analyst_estimates_defaults_to_annual() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        captured["path"] = request.url.path
        return httpx.Response(200, json=[{"date": "2025", "estimatedRevenueAvg": 400_000_000_000}])

    c = await _client_with_mock(handler)
    try:
        rows = await c.get_analyst_estimates("AAPL")
    finally:
        await c._client.aclose()

    assert captured["path"] == "/stable/analyst-estimates"
    assert captured["params"]["symbol"] == "AAPL"
    assert captured["params"]["period"] == "annual"
    assert len(rows) == 1


# --- All four statement endpoints hit /stable/* and pass symbol as a query param ----------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "expected_path"),
    [
        ("get_balance_sheet", "/stable/balance-sheet-statement"),
        ("get_cash_flow", "/stable/cash-flow-statement"),
        ("get_ratios", "/stable/ratios"),
    ],
)
async def test_statements_hit_stable_with_query_symbol(method: str, expected_path: str) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=[{"date": "2025-09-30"}])

    c = await _client_with_mock(handler)
    try:
        rows = await getattr(c, method)("AAPL", period="quarter", limit=4)
    finally:
        await c._client.aclose()

    assert captured["path"] == expected_path
    assert captured["params"]["symbol"] == "AAPL"
    assert captured["params"]["period"] == "quarter"
    assert len(rows) == 1


# --- Empty / null upstream response is tolerated ------------------------------------------


@pytest.mark.asyncio
async def test_endpoints_return_empty_list_when_upstream_returns_null() -> None:
    """Some FMP responses are JSON `null` for unknown symbols; helpers should coerce
    to an empty list rather than crash downstream parsers."""

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx.Response(json=None) emits an empty body, not the literal `null`. Use
        # raw content + the right content-type so resp.json() returns Python `None`.
        return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})

    c = await _client_with_mock(handler)
    try:
        assert await c.get_income_statement("ZZUNKNOWN") == []
        assert await c.get_balance_sheet("ZZUNKNOWN") == []
        assert await c.get_cash_flow("ZZUNKNOWN") == []
        assert await c.get_ratios("ZZUNKNOWN") == []
        assert await c.get_analyst_estimates("ZZUNKNOWN") == []
        assert await c.get_profile("ZZUNKNOWN") is None
        assert await c.get_dcf("ZZUNKNOWN") is None
    finally:
        await c._client.aclose()
