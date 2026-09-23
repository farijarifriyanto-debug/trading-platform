# Trading Platform

Modular, paper-first algorithmic trading and research platform. **Live trading remains disabled.**

## Phase 5 status

- CCXT public realtime ticker + OHLCV
- SHA-256 content-addressed historical datasets
- Strategy registry and deterministic parameter sweeps
- Paper execution with mandatory risk gates and audit
- VectorBT research integration
- NautilusTrader 2.x isolated simulation worker
- QuantConnect LEAN deterministic export pipeline
- Experiment registry and cross-engine comparison
- Responsive read-only operations dashboard
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

The execution-capable platform path remains **paper only**. Research and simulation engines cannot submit live exchange orders.

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

## Historical datasets

```bash
curl -X POST http://127.0.0.1:8000/datasets/ccxt \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","timeframe":"1h","limit":200,"refresh":false}'
```

Identical canonical content produces the same SHA-256 dataset ID. Dataset loads verify the hash before use.

## Paper trading

```bash
curl -X POST http://127.0.0.1:8000/paper/market-orders \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","side":"buy","quantity":0.001}'
```

Paper orders never reach the exchange.

## Persistence

Docker Compose persists `/data`:

```text
/data/paper-audit.jsonl
/data/historical/datasets/*.json
/data/historical/refs/*.json
/data/simulations/*.json
/data/experiments/*.json
/data/lean-exports/*
```

## Safety gates

- live exchange-order code does not exist
- paper execution is the only execution path
- Nautilus runs in an isolated simulation subprocess
- Nautilus jobs always return `live_mode=false`
- VectorBT has no execution adapter
- LEAN output is backtest-only and generated with `live-mode=false`
- no `lean live` invocation exists
- all paper orders pass `RiskManager`
- short selling is disabled by default in paper execution
- datasets, plans, exports, and experiments carry deterministic provenance
- dashboard is read-only

## Validation

Phase 5 validation includes:

- unit/API suite
- actual NautilusTrader 2.x worker execution
- public Kraken dataset -> Nautilus experiment end-to-end
- native/VectorBT/Nautilus comparison on the same dataset
- LEAN CLI installation/health
- deterministic LEAN bundle generation

Backtest and simulation results are research outputs, not profit guarantees.

## Next phase

Phase 6 focuses on AI/FinRL research: model/dataset provenance, walk-forward evaluation, robustness gates, candidate generation, and reproducible AI-assisted experiments. AI remains separated from order execution.
