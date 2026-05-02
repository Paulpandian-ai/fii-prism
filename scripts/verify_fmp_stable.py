"""Live verification of the FMP /stable/ migration.

Usage:
  FMP_API_KEY=<your-key> uv run python scripts/verify_fmp_stable.py [SYMBOL]

Hits the three endpoints the user already verified against their key:
  GET /stable/profile?symbol=AAPL
  GET /stable/income-statement?symbol=AAPL&period=quarter&limit=4
  GET /stable/discounted-cash-flow-valuation?symbol=AAPL

Prints a per-endpoint pass/fail summary. Exits non-zero if any endpoint returns 4xx/5xx
or an empty response shape that the parser can't tolerate. Empty DCF (`[]`) is reported
as a soft warning, not a failure — that's the documented behavior.
"""

from __future__ import annotations

import asyncio
import os
import sys

from fii_data_clients.fmp import FMPClient


async def main(symbol: str) -> int:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        print("[fail] FMP_API_KEY not set in environment", file=sys.stderr)
        return 2

    failed = 0
    async with FMPClient(api_key=key) as c:
        # 1. Profile
        try:
            profile = await c.get_profile(symbol)
            if profile and profile.get("companyName"):
                print(
                    f"[ ok ] /stable/profile         symbol={symbol} -> {profile.get('companyName')}"
                )
            else:
                print(
                    f"[fail] /stable/profile         symbol={symbol} -> empty/unexpected: {profile!r}"
                )
                failed += 1
        except Exception as exc:
            print(f"[fail] /stable/profile         symbol={symbol} -> {type(exc).__name__}: {exc}")
            failed += 1

        # 2. Income statement
        try:
            rows = await c.get_income_statement(symbol, period="quarter", limit=4)
            if rows and "revenue" in rows[0] and "date" in rows[0]:
                print(
                    f"[ ok ] /stable/income-statement symbol={symbol} -> {len(rows)} quarterly rows"
                    f", latest {rows[0].get('date')} revenue={rows[0].get('revenue'):,}"
                )
            else:
                print(
                    f"[fail] /stable/income-statement symbol={symbol} -> empty/missing fields: {rows!r}"
                )
                failed += 1
        except Exception as exc:
            print(f"[fail] /stable/income-statement symbol={symbol} -> {type(exc).__name__}: {exc}")
            failed += 1

        # 3. DCF (empty list → None + warning is the documented soft path).
        try:
            dcf = await c.get_dcf(symbol)
            if dcf is None:
                print(
                    f"[warn] /stable/discounted-cash-flow-valuation symbol={symbol}"
                    " -> None (FMP returned []; fmp_dcf_unavailable logged). Soft pass."
                )
            elif "dcf" in dcf:
                print(
                    f"[ ok ] /stable/discounted-cash-flow-valuation symbol={symbol}"
                    f" -> dcf={dcf.get('dcf')}"
                )
            else:
                print(
                    f"[fail] /stable/discounted-cash-flow-valuation symbol={symbol} -> unexpected shape: {dcf!r}"
                )
                failed += 1
        except Exception as exc:
            print(
                f"[fail] /stable/discounted-cash-flow-valuation symbol={symbol}"
                f" -> {type(exc).__name__}: {exc}"
            )
            failed += 1

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    sys.exit(asyncio.run(main(symbol)))
