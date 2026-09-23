# Phase 8B/8C Handoff — Binance Futures Live Canary Acceptance

Date: 2026-09-24
Base implementation: `phase8b-binance-futures-canary`
Acceptance closure: `phase8c-real-money-acceptance`
Target: Binance USD-M Futures fail-closed live boundary
Real-money canary: **PASS**
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
- One-shot arm is consumed before each exchange order.
- Version 0.8.1.

## Automated/read-only acceptance evidence

- Unit/API suite: **77 passed**.
- Python compile validation: **PASS**.
- Production preflight: **PASS**.
- Real Binance signed authentication/read: **PASS**.
- Binance Futures market load/read: **PASS**.
- Binance position mode: **one-way**.
- Binance API security posture gate: **PASS**.
- Allowlisted `BTC/USDT:USDT` verification: **PASS**.
- Read-only preflight attestation: **PASS**.

## Phase 8C real-money canary evidence

The canary was explicitly user-approved and manually initiated through the host-local arm + authenticated live endpoint.

Entry:
- request id: `canary-entry-1790185469`
- Binance order id: `1144212583950`
- side: BUY
- quantity: 0.001 BTC
- leverage/margin: 1x / isolated
- status: **FILLED**
- average fill: 84,210.4 USDT
- reduce-only: false

Close:
- request id: `canary-close-1790185559`
- Binance order id: `1144213516421`
- side: SELL
- quantity: 0.001 BTC
- status: **FILLED**
- average fill: 84,263.1 USDT
- reduce-only: **true**

Final exchange state verified directly after the close:
- BTCUSDT position amount: **0.000**
- open BTCUSDT orders: **0**
- entry and close each filled exactly 0.001 BTC
- two related trades found
- reported fees: 0.08423675 USDT total
- gross price movement P/L for the 0.001 BTC round trip: +0.0527 USDT
- net after the reported trade fees: -0.03153675 USDT

Final local state:
- both request ids are present exactly once in the durable live ledger
- both records are `closed` with no error
- one-shot arm consumed after each order
- `armed=false`
- `kill_switch=true`
- temporary acceptance API listener on `127.0.0.1:48070` was stopped after verification

## Locked safety boundaries

- There is no HTTP arm endpoint.
- A fresh signed private preflight is required before host-local arming.
- One arm authorizes at most one new live order.
- Research/AI/FinRL/simulation paths cannot arm or submit live orders.
- Contract SELL is reduce-only under the no-short policy.
- No credential value is committed to Git.
- Docker Compose remains hard-pinned to `TRADING_LIVE_ENABLED=0`.
- Acceptance does not itself authorize unattended live trading or production cutover.

## Acceptance result

**PHASE 8 REAL-MONEY CANARY = PASS.**

The implementation has demonstrated a complete real Binance Futures round trip through the platform: preflight -> one-shot arm -> authenticated entry -> automatic fail-closed state -> fresh preflight -> second one-shot arm -> reduce-only close -> flat-position verification -> fail-closed shutdown.

The next decision is operational production rollout/hardening, not another proof-of-execution canary.
