# Trading Platform

Modular, paper-first algorithmic trading and research platform. Phase 8 contains a fail-closed live execution boundary, but **live capability remains disabled by default and no real order is enabled by this repository alone.**

## Phase 8 status

- CCXT public realtime ticker + OHLCV
- SHA-256 content-addressed historical datasets
- Strategy registry and deterministic parameter sweeps
- Paper execution with mandatory risk gates and audit
- VectorBT research integration
- NautilusTrader 2.x isolated simulation worker
- QuantConnect LEAN deterministic export pipeline
- Experiment registry and cross-engine comparison
- Responsive read-only operations dashboard
- AI candidate registry with deterministic SHA-256 provenance
- Isolated scikit-learn walk-forward research worker
- Explicit robustness gate: `RESEARCH_PASS` / `REVIEW_REQUIRED`
- FinRL-X isolated out-of-sample backtest integration
- Model artifact SHA-256 provenance
- SQLite WAL durable paper portfolio state
- Durable research job queue with leases, retries, idempotency, cancellation, and stale-worker recovery
- Mutation Bearer authentication gate, request-size limit, rate limit, and security headers
- Readiness and Prometheus-compatible operational metrics
- Verified backup/restore tooling with per-file SHA-256 manifest
- Hardened systemd candidate units and Docker API candidate
- Fail-closed CCXT private live execution boundary (disabled by default)
- Host-local, TTL-limited one-shot arming; there is no HTTP arm endpoint
- Live symbol allowlist, per-order/position/daily notional limits, balance/precision/freshness checks
- Durable live order ledger with client-order idempotency and unknown-outcome quarantine
- Startup fail-closed recovery, reconciliation, kill switch, and emergency open-order cancellation
- FastAPI, Docker/Compose, GitHub Actions CI

## Architecture

```text
Exchange public data
        |
       CCXT
        |
Historical Dataset Store
        |
   Experiment Spec
   /      |       \
Native  VectorBT  Nautilus subprocess
   \      |       /
    Experiment Registry
          |
     Comparison API
          |
   Dashboard / Metrics

Dataset -> LEAN export bundle -> LEAN CLI/Docker (optional external runner)
```

Research and simulation engines still cannot submit live exchange orders. The separate Phase 8 live boundary can only submit when live capability is explicitly enabled in host configuration **and** a short-lived host-local one-shot arm is active. Defaults remain disabled and disarmed.

## Run control plane

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,crypto,research]"
pytest -q
uvicorn trading_platform.api:app --reload
```

Open:

- Dashboard: `http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/docs`
- Worker health: `http://127.0.0.1:8000/workers/health`

## NautilusTrader isolated worker

NautilusTrader 2.x currently requires Python 3.12+. Keep it outside the control-plane environment:

```bash
uv venv --python python3.12 ~/.venvs/trading-nautilus
cd /tmp
uv pip install --python ~/.venvs/trading-nautilus/bin/python --pre nautilus_trader pandas

export NAUTILUS_WORKER_PYTHON=$HOME/.venvs/trading-nautilus/bin/python
```

The API invokes `workers/nautilus_worker.py` as a subprocess with a JSON job and a separate JSON result file. The worker:

- consumes an immutable dataset
- creates a synthetic simulation instrument
- replays external OHLCV bars through NautilusTrader's `BacktestEngine`
- uses L1 bar execution
- runs a long-only SMA strategy
- returns fills, positions, equity, engine version, and return
- always reports `live_mode=false`

NautilusTrader is not installed into the API container and has no exchange credentials.

## Experiments

Create one experiment:

```bash
curl -X POST http://127.0.0.1:8000/experiments \
  -H 'content-type: application/json' \
  -d '{
    "dataset_id":"<dataset_id>",
    "engine":"nautilus",
    "strategy":"sma_trend",
    "parameters":{"fast":5,"slow":20},
    "initial_cash":100000,
    "quantity":0.001
  }'
```

Run it:

```bash
curl -X POST http://127.0.0.1:8000/experiments/<experiment_id>/run
```

Supported executable experiment engines:

- `native`
- `vectorbt` when the `research` extra is installed
- `nautilus` when the isolated Python 3.12 worker environment is installed

Compare completed results:

```bash
curl 'http://127.0.0.1:8000/experiments/compare?dataset_id=<dataset_id>'
```

Experiment IDs are deterministic SHA-256 hashes of dataset, engine, strategy, parameters, initial cash, and quantity.

Different engines have different execution semantics, so close-but-not-identical results are expected. The registry preserves those differences instead of forcing them to match.

## LEAN export pipeline

Install the official LEAN CLI separately:

```bash
uv venv --python python3.12 ~/.venvs/trading-lean
uv pip install --python ~/.venvs/trading-lean/bin/python lean
~/.venvs/trading-lean/bin/lean --version
```

Create a deterministic backtest-only bundle:

```bash
curl -X POST http://127.0.0.1:8000/lean/exports \
  -H 'content-type: application/json' \
  -d '{
    "dataset_id":"<dataset_id>",
    "parameters":{"fast":5,"slow":20},
    "initial_cash":100000
  }'
```

Each export contains:

```text
main.py
config.json
manifest.json
<dataset_id>.csv
```

The generated configuration sets `live-mode=false`. LEAN CLI local backtests use the official Docker engine and require a configured LEAN workspace. The platform never invokes `lean live`.

## AI / FinRL research

AI research is isolated from execution. Candidate specs bind the immutable dataset ID, feature version, model family, seed, folds, hyperparameters, and robustness policy into a deterministic candidate ID.

Worker health:

```bash
curl http://127.0.0.1:8000/ai/health
curl http://127.0.0.1:8000/ai/finrlx/health
```

The reference ML worker uses chronological walk-forward folds with a seeded random forest. It records fold metrics, out-of-sample predictions, scikit-learn version, hyperparameters, and a SHA-256 of the persisted model artifact. The robustness gate is research-only and never authorizes orders.

A completed candidate can be evaluated by the isolated FinRL-X backtest worker. FinRL-X receives only the immutable dataset and out-of-sample predictions and always returns `execution_enabled=false` and `live_mode=false`.

Phase 6 live validation uses a synthetic immutable dataset and verifies both the real scikit-learn worker and FinRL-X worker end-to-end.

## Historical datasets

```bash
curl -X POST http://127.0.0.1:8000/datasets/ccxt \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","timeframe":"1h","limit":200,"refresh":false}'
```

Identical canonical content produces the same SHA-256 dataset ID. Dataset loads verify the hash before use.


## Phase 8 live execution boundary

Live execution is deliberately separated from paper/research. The default configuration is:

```text
TRADING_LIVE_ENABLED=0
kill_switch=true
armed=false
one_shot_arm=true
```

There is intentionally **no HTTP endpoint to arm live trading**. Arming can only be performed from the host after the environment has been deliberately configured:

```bash
python scripts/live-preflight.py --private-read-check
python scripts/live-control.py status
python scripts/live-control.py arm --ttl 60 --confirm ENABLE_REAL_MONEY_ORDER_WINDOW
```

The private preflight performs read-only credential/exchange checks and never creates an order. A successful private preflight writes a short-lived, non-secret attestation tied to the exact live configuration; host-local arming refuses stale or mismatched attestations. A live order still requires authenticated HTTP, an allowlisted symbol, one-way position mode for derivatives, exchange precision/minimum validation, a fresh quote, deterministic risk limits, available balance, a daily notional budget, and a unique request id. The arm is consumed atomically before the exchange call, so one arm authorizes at most one new live order.

For Binance USD-M Futures, CCXT uses unified contract symbols and `future` market type:

```text
TRADING_LIVE_EXCHANGE=binance
TRADING_LIVE_MARKET_TYPE=future
TRADING_LIVE_ALLOWED_SYMBOLS=BTC/USDT:USDT
```

The Binance adapter reads the current one-way/hedged position mode and rejects hedged mode. The private preflight also requires API reading permission, Futures permission for the Futures profile, IP restriction enabled, and withdrawals disabled. Sell orders on contract markets are sent as `reduceOnly`, so this Phase 8 boundary cannot use a sell request to open a short position.

```bash
curl -X POST http://127.0.0.1:48070/live/orders \
  -H 'Authorization: Bearer <TRADING_API_KEY>' \
  -H 'content-type: application/json' \
  -d '{"request_id":"manual_canary_001","symbol":"BTC/USDT:USDT","side":"buy","quantity":0.001}'
```

Do not run that request until a real-money canary is separately approved. Reusing the same request id cannot create a second order. If an exchange call times out after reservation, the ledger records `unknown` and refuses to retry that request id until reconciliation.

Operational controls:

```bash
# Always available locally; immediately blocks new live orders.
python scripts/live-control.py disarm

# Authenticated API kill switch; when live capability is configured it also attempts
# to cancel this platform's tracked open orders on allowlisted symbols.
curl -X POST http://127.0.0.1:48070/live/emergency-stop \
  -H 'Authorization: Bearer <TRADING_API_KEY>'

# Reconcile local unknown/open records against recent exchange orders.
curl -X POST http://127.0.0.1:48070/live/reconcile \
  -H 'Authorization: Bearer <TRADING_API_KEY>'
```

API process startup and systemd shutdown both disarm live execution. In-flight `reserved`/`submitted` records become `unknown` after process restart and require reconciliation. Backup snapshots also force the restored live control state to disarmed.

The exchange API key should be scoped to only the query/trading permissions required for this service and should not have withdrawal permissions. Real credentials belong only in the host's protected environment file, never in Git.

## Paper trading

```bash
curl -X POST http://127.0.0.1:8000/paper/market-orders \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","side":"buy","quantity":0.001}'
```

Paper orders never reach the exchange.

## Durable runtime and jobs

Mutable runtime state is now separated from immutable research artifacts:

```text
/data/runtime.sqlite3        # paper cash + positions, WAL/FULL sync
/data/jobs.sqlite3           # durable queue, leases/retry/recovery
/data/live.sqlite3           # fail-closed live control + idempotent order ledger
/data/paper-audit.jsonl
/data/historical/...         # immutable content-addressed datasets
/data/simulations/...
/data/experiments/...
/data/ai-candidates/...
/data/models/...
/data/lean-exports/...
```

Paper fills persist the portfolio immediately. The research worker queue supports idempotent enqueue, bounded retries, worker leases, stale RUNNING recovery, cancellation, and terminal results. Queueable workloads are experiment runs, AI candidate training, and FinRL-X evaluation; paper orders are deliberately not background/autonomous jobs.

Run the durable worker with:

```bash
python workers/job_worker.py --poll-seconds 1 --lease-seconds 300
```

## Security and readiness

Production candidate configuration sets `TRADING_REQUIRE_AUTH=1`. Mutating HTTP methods then require `Authorization: Bearer <TRADING_API_KEY>`. The API also enforces a configurable body-size ceiling and mutation rate limit and emits defensive browser headers.

```bash
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/security/status
curl http://127.0.0.1:8000/metrics/prometheus
```

`/ready` verifies paper/runtime and live-ledger SQLite integrity plus writable durable storage. No secret value is returned by the status endpoint. Authenticated live status is available at `/live/status` when API auth is enabled.

## Backup and recovery

Create and verify a consistent snapshot:

```bash
python scripts/backup-data.py create /secure-backups/trading-platform.tar.gz --data-root /var/lib/trading-platform
python scripts/backup-data.py verify /secure-backups/trading-platform.tar.gz
```

Restore drills must target an empty staging directory first:

```bash
python scripts/backup-data.py restore /secure-backups/trading-platform.tar.gz /tmp/trading-restore-drill
```

SQLite databases are copied through SQLite's backup API. Live-control snapshots are forced to the disarmed state before archiving. Every other persisted file is covered by a SHA-256 manifest inside the archive.

## Production candidate deployment

`deploy/systemd/` contains separate API and durable-worker units. The worker has `PrivateNetwork=true`; external simulation/ML environments remain the isolated host-native venvs established in Phases 5-6. `deploy/trading-platform.env.example` documents the required environment without real secrets.

Docker Compose remains an API-only candidate and now uses loopback binding, read-only root filesystem, dropped capabilities, no-new-privileges, tmpfs, and `/ready` health checks.

Run `scripts/production-preflight.sh` before any candidate promotion. Phase 8 code does **not** perform a production cutover, install credentials, arm the system, or place a real order.

## Safety gates

- live capability defaults to disabled and the durable kill switch defaults to engaged
- there is no HTTP arm endpoint; arming is host-local, confirmation-gated, TTL-limited, and one-shot
- API startup and systemd shutdown disarm live execution
- live GET/order/control endpoints require Bearer auth when production auth is enabled
- live symbols are allowlisted and shorts are disabled
- exchange amount precision/minimums, quote freshness, available balances, per-order, position, and daily notional limits are checked before reservation
- live request ids are durable/idempotent and map to a bounded exchange client-order id
- an arm is consumed in the same SQLite transaction that reserves the order
- ambiguous exchange outcomes become `unknown`; automatic retry is forbidden
- restart recovery converts in-flight reservations to `unknown` for reconciliation
- emergency stop disarms first, then attempts cancellation only of this platform's tracked open orders on allowlisted symbols
- backup/restore cannot restore an armed state
- AI, FinRL, Nautilus, VectorBT, LEAN, and durable research jobs have no path to the live-order endpoint
- Docker Compose remains hard-pinned to `TRADING_LIVE_ENABLED=0`

## Validation

Phase 8 validation includes:

- unit/API suite
- actual NautilusTrader 2.x worker execution
- public Kraken dataset -> Nautilus experiment end-to-end
- native/VectorBT/Nautilus comparison on the same dataset
- LEAN CLI installation/health
- deterministic LEAN bundle generation
- real isolated scikit-learn walk-forward model training
- model artifact hashing and candidate provenance
- real FinRL-X out-of-sample backtest worker execution
- AI/FinRL safety assertions (`live_mode=false`, `execution_enabled=false`)
- persistent paper-state restart test
- durable queue retry/idempotency/stale-lease recovery tests
- queue -> worker -> native experiment end-to-end test
- backup -> verify -> restore recovery drill
- authenticated HTTP mutation gate test
- 200-request concurrent readiness/metrics stress smoke
- fail-closed live-state and restart tests
- one-shot arm and idempotent-order tests
- stale quote, balance, short, and daily-limit rejection tests
- ambiguous-outcome quarantine and reconciliation tests
- emergency-stop cancellation test
- live backup/restore disarm test
- live HTTP arm endpoint absence test

Backtest and simulation results are research outputs, not profit guarantees.

## Next phase

Phase 8A implements the fail-closed live execution boundary. Phase 8B adds Binance USD-M Futures support and a fresh signed read-only preflight attestation before host-local arming. The remaining acceptance step is an explicitly approved real-money canary: inspect the current account state, select one allowlisted order within the configured caps and exchange minimums, arm once, submit once, reconcile, and immediately disarm. No real-money canary or production cutover is performed automatically.
