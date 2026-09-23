# Production Rollout Hardening

Date: 2026-09-24
Release target: 0.9.0
Scope: make the accepted Phase 8 live boundary operational without enabling autonomous trading.

## Production operating model

- Binance USD-M Futures remains the accepted live venue.
- The API is host-local only on 127.0.0.1:48070.
- Startup always disarms first.
- Startup requires a signed private read-only exchange preflight.
- Shutdown always disarms.
- One-shot arm remains mandatory for every real-money order.
- Contract SELL remains reduce-only under the no-short policy.
- Research, AI, FinRL, simulation, and strategy paths cannot arm live execution.
- There is no HTTP arm endpoint.
- Reboots/restarts do not leave the platform armed.

## Operator usability

A host-local operator CLI now covers:
- status;
- signed private preflight;
- read-only order preview;
- reconciliation;
- emergency stop;
- disarm;
- explicitly confirmed one-shot order submission.

Real-money submission is never scheduled or selected by the platform. It requires an explicit operator command and exact confirmation phrase.

## Production fixes

Reconciliation and emergency-stop exchange discovery now query each allowlisted symbol explicitly. This avoids relying on exchanges that do not support account-wide closed-order history without a symbol.

## Deployment artifacts

- deploy/systemd/trading-platform-live-user.service
- deploy/binance-futures.env.example
- scripts/live-operator.py

The systemd user service is designed for a lingering botadmin user and a writable state directory at ~/.local/state/trading-platform.

## Validation before deployment

- full test suite;
- Python compile validation;
- operator CLI parse/help smoke;
- real Binance read-only preview;
- git diff check;
- CI on PR and main.

No additional real-money order is required for this rollout because Phase 8 already completed the accepted round-trip canary.
