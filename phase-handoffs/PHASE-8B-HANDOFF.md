# Phase 8B Handoff — Binance Futures Read-Only Canary Readiness

Date: 2026-09-24
Branch: `phase8b-binance-futures-canary`
Target: Binance USD-M Futures live boundary readiness
Real-money order placed: **NO**
Production cutover: **NOT PERFORMED**

## Implemented

- CCXT live market-type configuration (`spot`, `future`, `delivery`).
- Binance USD-M Futures support with unified symbols such as `BTC/USDT:USDT`.
- Contract-position reads and one-way/hedged-mode validation.
- Futures sell orders are forced to `reduceOnly` under the no-short Phase 8 policy.
- Exchange minimum-cost validation in addition to amount precision/minimum checks.
- Binance private API security posture validation:
  - read permission required;
  - Futures permission required for Futures profile;
  - IP restriction required;
  - withdrawals must be disabled.
- Fresh read-only preflight attestation bound to the exact live configuration.
- Host-local arm refuses missing, stale, or configuration-mismatched preflight attestations.
- Version advanced to 0.8.1.

## Acceptance evidence

- Unit/API suite: **77 passed**.
- Python compile validation: **PASS**.
- Production preflight: **PASS**, live trading OFF.
- Real Binance signed read-only authentication: **PASS**.
- Binance Futures market load/read: **PASS**.
- Binance position mode: **one-way**.
- Binance API security posture gate: **PASS**.
- Allowlisted Binance Futures symbol verification: **PASS**.
- Read-only preflight attestation: **PASS**.
- Durable live control after preflight: **disarmed / kill switch engaged**.
- No exchange order was created during Phase 8B acceptance.

## Locked safety boundaries

- There is still no HTTP arm endpoint.
- A private read-only preflight must be fresh before host-local arming.
- One arm authorizes at most one new live order.
- Research/AI/FinRL/simulation paths cannot arm or submit live orders.
- No credential value is committed to Git.
- No real-money canary is part of this handoff.

## Remaining acceptance step

A real-money canary is intentionally separate: inspect current account state, choose one allowlisted order that satisfies exchange minimums and configured notional caps, run a fresh private preflight, arm once, submit once, reconcile, then disarm. That step requires explicit real-money execution approval and is not implied by Phase 8B completion.
