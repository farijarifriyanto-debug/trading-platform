# Trading Platform

Modular, paper-first algorithmic trading and research platform. **Live trading remains disabled.**

## Phase 4 status

- CCXT public realtime ticker + OHLCV
- Content-addressed historical datasets with SHA-256 IDs and integrity checks
- Strategy registry and deterministic parameter sweeps
- Paper execution with mandatory risk gates and persistent audit
- Optional VectorBT research adapter
- **NautilusTrader simulation boundary**
- **QuantConnect LEAN simulation boundary**
- Immutable, content-addressed simulation plans
- **Built-in responsive operations dashboard**
- Control-plane metrics
- FastAPI, Docker/Compose, GitHub Actions CI

## Architecture

```text
Exchange public data
        |
       CCXT
        |
Historical Dataset Store
   |                |
Strategy         Research / Sweep
   |                |
   +---- Simulation Boundaries ----+
   |          |                    |
 Paper     Nautilus              LEAN
 Broker    backtest plan         backtest plan
   |
Risk -> Portfolio -> Audit
          |
      API / Dashboard
```

NautilusTrader and LEAN are currently **simulation boundaries**, not execution services. The platform generates reproducible backtest plans but does not start either external engine and does not expose live execution.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,crypto]"
pytest -q
uvicorn trading_platform.api:app --reload
```

Open:

- Dashboard: `http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

## Dashboard

The built-in dashboard refreshes every 10 seconds and shows:

- historical dataset count and metadata
- simulated paper cash and open positions
- audit event count and recent events
- available simulation engines
- paper portfolio state

It uses the same read-only control-plane API endpoints and has no order-entry controls.

## Historical datasets

Capture or reuse public CCXT data:

```bash
curl -X POST http://127.0.0.1:8000/datasets/ccxt \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","timeframe":"1h","limit":200,"refresh":false}'
```

The response includes a SHA-256 `dataset_id`. Identical canonical content produces the same ID. Loading a dataset re-verifies the hash.

## Strategy research

Catalog:

```bash
curl http://127.0.0.1:8000/strategies
```

Parameter sweep against an immutable dataset:

```bash
curl -X POST http://127.0.0.1:8000/research/sweeps/sma \
  -H 'content-type: application/json' \
  -d '{"dataset_id":"<dataset_id>","quantity":0.001,"fast_values":[3,5,10],"slow_values":[20,50]}'
```

Research output is not a profitability guarantee.

## NautilusTrader / LEAN simulation boundaries

Available engines:

```bash
curl http://127.0.0.1:8000/simulations/engines
```

Create a NautilusTrader backtest plan:

```bash
curl -X POST http://127.0.0.1:8000/simulations/nautilus/plans \
  -H 'content-type: application/json' \
  -d '{"dataset_id":"<dataset_id>","strategy":"sma_trend","parameters":{"fast":5,"slow":20}}'
```

Create a LEAN backtest plan:

```bash
curl -X POST http://127.0.0.1:8000/simulations/lean/plans \
  -H 'content-type: application/json' \
  -d '{"dataset_id":"<dataset_id>","strategy":"sma_trend","parameters":{"fast":5,"slow":20}}'
```

Each plan receives a deterministic SHA-256 `plan_id` and is persisted for provenance.

The Nautilus boundary mirrors the project's documented high-level backtest concepts: venue, data, engine configuration, explicit fee model, and shutdown-on-error. The LEAN boundary emits a backtest-only configuration with `live-mode=false`.

These are deliberately adapter contracts. Heavy external engines are not bundled into the API container yet.

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
```

## Safety gates

- only paper execution exists
- no live exchange-order code path
- simulation plans always report `live_mode=false`
- Nautilus/LEAN adapters cannot submit orders
- short selling disabled by default in the paper broker
- paper buying power enforced
- all paper orders pass `RiskManager`
- datasets and simulation plans are hash-addressed
- paper fills and strategy steps are audit logged
- dashboard has no order-entry controls

## Next phase

1. Run NautilusTrader as an isolated optional simulation worker.
2. Add LEAN worker/export pipeline without coupling it to paper execution.
3. Experiment registry: dataset + strategy + parameters + engine + result provenance.
4. Compare sweep, VectorBT, Nautilus, and LEAN results.
5. AI/FinRL research workflow with dataset/model provenance.
6. Keep live trading behind a separate future security and risk review.
