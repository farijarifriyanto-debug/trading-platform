# Trading Platform

Modular, paper-first algorithmic trading platform. Live trading is intentionally disabled in v0.1.

## Included

- FastAPI control plane
- SMA reference strategy and deterministic backtest
- Mandatory pre-trade risk gate
- Paper broker with fee/slippage simulation
- Portfolio/cash/position accounting
- Optional CCXT market-data adapter
- AI research boundary with no direct execution path
- Docker + GitHub Actions CI

## Architecture

`Market/Broker -> Data -> Strategy/Backtest/AI Research -> Risk -> Paper Execution -> Portfolio -> API`

External engines stay behind adapters/sidecars so their licenses and operational boundaries remain explicit: CCXT, VectorBT, Freqtrade, Jesse, Hummingbot, NautilusTrader/LEAN, and FinRL.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
uvicorn trading_platform.api:app --reload
```

API docs: `http://127.0.0.1:8000/docs`

## Next phases

1. Historical data service + VectorBT adapter.
2. Realtime CCXT market data.
3. Paper trading end-to-end with persisted audit log.
4. NautilusTrader/LEAN multi-asset adapter.
5. FinRL/LLM research layer with reproducible model versions.
6. Dashboard and explicit live-trading enablement gates.
