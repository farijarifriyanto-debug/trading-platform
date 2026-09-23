# Trading Platform

Modular, paper-first algorithmic trading platform. **Live trading remains disabled.**

## Phase 2 status

- CCXT public realtime ticker + OHLCV market data
- Side-aware paper market orders (ask for buy, bid for sell, fallback to last)
- SMA strategy step: market data -> signal -> risk -> paper execution -> portfolio
- Long-only default risk policy with notional limits
- Persistent append-only JSONL paper audit log
- Optional VectorBT research adapter with no execution capability
- FastAPI control plane, Docker, Compose and GitHub Actions CI

## Architecture

`Exchange public data -> CCXT -> Strategy / VectorBT research -> Risk -> Paper Broker -> Portfolio -> Audit/API`

The AI and VectorBT research layers have no direct execution path. Live trading is intentionally absent.

## Run core API + CCXT

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,crypto]"
pytest -q
uvicorn trading_platform.api:app --reload
```

API docs: `http://127.0.0.1:8000/docs`

## Realtime public-data examples

```bash
curl 'http://127.0.0.1:8000/market/kraken/ticker?symbol=BTC/USD'

curl -X POST http://127.0.0.1:8000/paper/market-orders \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","side":"buy","quantity":0.001}'

curl -X POST http://127.0.0.1:8000/paper/strategy/sma/step \
  -H 'content-type: application/json' \
  -d '{"exchange":"kraken","symbol":"BTC/USD","quantity":0.001,"timeframe":"1h","limit":200,"fast":5,"slow":20}'
```

These endpoints use public exchange data only; no exchange API key is required. Paper orders never reach the exchange.

## VectorBT research

Install the optional research dependency:

```bash
pip install -e ".[research]"
```

Then `POST /research/vectorbt/sma` with a price series. The adapter follows VectorBT's documented `MA.run -> ma_crossed_above/below -> Portfolio.from_signals` flow.

## Safety gates

- default execution mode is paper only
- live trading code path is not present
- short selling disabled by default
- every paper order passes `RiskManager`
- market-order price is sourced from public market data, not user-supplied
- persistent audit trail records strategy steps and fills

## Next phase

1. Historical market-data cache and reproducible datasets.
2. Strategy registry + parameter sweeps.
3. NautilusTrader/LEAN adapter boundary for multi-asset simulation.
4. Dashboard/metrics.
5. AI/FinRL research workflow with reproducible model/data versions.
6. Explicit, separately reviewed live-trading gate only after paper acceptance.
