# Trading Platform

Modular, paper-first algorithmic trading platform. **Live trading remains disabled.**

## Phase 3 status

- CCXT public realtime ticker + OHLCV market data
- Content-addressed historical datasets with SHA-256 dataset IDs
- Cache refs for exchange/symbol/timeframe/limit requests
- Dataset integrity verification on every load
- Strategy registry with built-in `sma_trend`
- Deterministic SMA parameter sweeps
- Side-aware paper market orders
- Risk gate with long-only default, notional limits, and paper buying-power checks
- Persistent append-only JSONL paper audit log
- Optional VectorBT research adapter with no execution capability
- FastAPI control plane, Docker/Compose, and GitHub Actions CI

## Architecture

`Exchange public data -> CCXT -> Historical Dataset Cache -> Strategy / Research -> Risk -> Paper Broker -> Portfolio -> Audit/API`

Research components never submit exchange orders. Live execution code is intentionally absent.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,crypto]"
pytest -q
uvicorn trading_platform.api:app --reload
```

API docs: `http://127.0.0.1:8000/docs`

## Realtime public market data

```bash
curl 'http://127.0.0.1:8000/market/kraken/ticker?symbol=BTC/USD'
curl 'http://127.0.0.1:8000/market/kraken/ohlcv?symbol=BTC/USD&timeframe=1h&limit=50'
```

No exchange API key is required for these public-data endpoints.

## Reproducible historical datasets

Capture or reuse a cached market-data snapshot:

```bash
curl -X POST http://127.0.0.1:8000/datasets/ccxt \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","timeframe":"1h","limit":200,"refresh":false}'
```

The response contains a 64-character SHA-256 `dataset_id`. The ID is derived from the canonical dataset content, so identical content produces the same ID.

List snapshots:

```bash
curl http://127.0.0.1:8000/datasets
```

Load and integrity-check one snapshot:

```bash
curl http://127.0.0.1:8000/datasets/<dataset_id>
```

Set `refresh: true` to fetch the exchange again and update the cache ref. Immutable content-addressed snapshots remain available by ID.

## Strategy registry

```bash
curl http://127.0.0.1:8000/strategies
```

Current built-in strategy:

- `sma_trend`: fast/slow simple moving-average trend state

Generic paper strategy step:

```bash
curl -X POST http://127.0.0.1:8000/paper/strategies/sma_trend/step \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","quantity":0.001,"timeframe":"1h","limit":200,"parameters":{"fast":5,"slow":20}}'
```

## Deterministic parameter sweep

Run against an immutable dataset:

```bash
curl -X POST http://127.0.0.1:8000/research/sweeps/sma \
  -H 'content-type: application/json' \
  -d '{"dataset_id":"<dataset_id>","quantity":0.001,"fast_values":[3,5,10],"slow_values":[20,50]}'
```

Or provide an inline price series instead of `dataset_id`. Results are sorted deterministically by ending equity, then fast/slow parameters.

Backtest output is research data, not a profitability guarantee.

## Paper trading

Paper market order:

```bash
curl -X POST http://127.0.0.1:8000/paper/market-orders \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","side":"buy","quantity":0.001}'
```

Paper orders never reach the exchange.

## VectorBT research

Install the optional research dependency:

```bash
pip install -e ".[research]"
```

Then use `POST /research/vectorbt/sma`. The adapter follows VectorBT's documented `MA.run -> ma_crossed_above/below -> Portfolio.from_signals` flow.

## Persistence

Docker Compose mounts `/data` into a named volume. The following survive container restarts:

- `/data/paper-audit.jsonl`
- `/data/historical/datasets/*.json`
- `/data/historical/refs/*.json`

## Safety gates

- default and only execution mode is paper
- live trading code path is not present
- short selling disabled by default
- every paper order passes `RiskManager`
- paper buys cannot exceed available simulated cash
- market-order price is sourced from public market data
- paper fills and strategy steps are audit logged
- historical datasets are immutable and hash verified

## Validation

Phase 3 adds offline tests for dataset hashing, tamper detection, cache behavior, strategy registry, parameter sweeps, API smoke tests, and traversal protection. A live public-data smoke test also verifies Kraken through CCXT without an API key.

## Next phase

1. NautilusTrader/LEAN adapter boundary for multi-asset simulation.
2. Dashboard for datasets, sweeps, paper portfolio, and audit events.
3. Metrics and experiment comparison.
4. AI/FinRL research workflow with dataset/model provenance.
5. Explicit, separately reviewed live-trading gate only after paper acceptance.
