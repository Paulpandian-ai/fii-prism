# DISCLAIMERS

## What this is

FII-PRISM is a **personal investment-research tool** built by and for Paul Balasubramanian. It
helps me think more rigorously about my own portfolio. It does **not** make trades, manage
money, or accept funds from anyone.

## What this is not

This is **NOT investment advice.** No part of the system constitutes a recommendation, offer,
or solicitation to buy, sell, or hold any security. The outputs are educational and exploratory.

I am not a registered investment adviser. I do not have a fiduciary relationship with anyone
who reads, uses, or sees outputs from this system.

## Specific limits

1. **All AI outputs end with**: _"For educational purposes only. Not investment advice."_ This
   line is enforced by an `OrchestratorFinalOutput` schema validator — synthesis cannot save a
   run without it.
2. **The model can be wrong.** Even with citations, the model may misinterpret a filing,
   miscount a metric, or anchor on stale data. Every CitedNumber + CitedClaim links to its
   source so the human can verify. Trust the source, not the model.
3. **Past performance ≠ future results.** The decision-journal hit-rate and alpha numbers
   describe what happened; they don't guarantee what will happen.
4. **Backtesting limits.** The journal compares against SPY, not against a risk-equivalent
   benchmark. It does not adjust for transaction costs, taxes, slippage, or position sizing
   beyond what the user records.
5. **Tax / legal**: nothing in `calculate_tax_loss_harvest` is tax advice. The wash-sale rule
   is not modeled. Consult a CPA or tax attorney for actual tax decisions.
6. **Single-user data**: positions, watchlist, cash balance, chat history — all of it is
   single-user state for me. The system has no multi-tenant boundaries today.
7. **Data quality**: prices come from Polygon, fundamentals from FMP, filings from SEC EDGAR,
   macro from FRED. Each provider has known data gaps. The system flags `not_yet_ingested` and
   `data_missing` rather than guessing.
8. **No realtime trading**: there is no order routing, no broker integration, no position
   reconciliation against a brokerage. Positions are typed in by hand.

## Prompt-injection defense

News content and 10-K text are wrapped in `<untrusted_news_content>` and `<filing_text>` tags
respectively. The system prompts instruct specialists to treat tag-wrapped content as data,
never as instructions. A regression suite of 20 known injection payloads (see
`packages/agents/tests/test_prompt_injection_corpus.py`) exercises the structural detector on
every CI run.

If you're a security researcher who finds an injection that gets past these safeguards,
please open an issue on GitHub.

## When this disclaimer applies

Always. On the home page, on every analysis page (`<Disclaimer />`), on every advisor turn
(end of message), on every API response that surfaces a recommendation, and at the end of
this document.

> _For educational purposes only. Not investment advice._
