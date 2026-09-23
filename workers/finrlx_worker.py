#!/usr/bin/env python3
import argparse
import importlib.metadata
import json
import os
import sys
from pathlib import Path


def run(job):
    source = Path(job["finrlx_source"])
    if not source.is_dir():
        raise RuntimeError(f"FinRL-X source not found: {source}")
    sys.path.insert(0, str(source))

    import pandas as pd
    from src.backtest.backtest_engine import BacktestConfig, BacktestEngine

    candles_by_ts = {int(c["timestamp"]): c for c in job["candles"]}
    predictions = job["oos_predictions"]
    rows = []
    for item in predictions:
        timestamp = int(item["timestamp"])
        candle = candles_by_ts.get(timestamp)
        if candle is None:
            continue
        rows.append(
            {
                "timestamp": timestamp,
                "close": float(candle["close"]),
                "weight": 1.0 if int(item["prediction"]) == 1 else 0.0,
            }
        )
    if len(rows) < 2:
        raise ValueError("not enough aligned OOS predictions for FinRL-X backtest")

    index = pd.to_datetime([row["timestamp"] for row in rows], unit="ms", utc=True).tz_localize(None)
    ticker = job["symbol"].replace("/", "")
    price_data = pd.DataFrame({ticker: [row["close"] for row in rows]}, index=index)
    weights = pd.DataFrame({ticker: [row["weight"] for row in rows]}, index=index)
    config = BacktestConfig(
        start_date=index[0].strftime("%Y-%m-%d"),
        end_date=index[-1].strftime("%Y-%m-%d"),
        initial_capital=float(job.get("initial_cash", 100_000.0)),
        transaction_cost=float(job.get("transaction_cost", 0.0005)),
        benchmark_tickers=[],
    )
    result = BacktestEngine(config).run_backtest(
        "TradingPlatformAICandidate",
        price_data,
        weights,
    )
    metrics = {key: float(value) for key, value in result.metrics.items()}
    return {
        "engine": "finrl-x",
        "finrl_trading_version": importlib.metadata.version("finrl-trading"),
        "source_commit": job.get("finrlx_source_commit"),
        "dataset_id": job["dataset_id"],
        "candidate_id": job["candidate_id"],
        "symbol": job["symbol"],
        "oos_rows": len(rows),
        "metrics": metrics,
        "annualized_return": float(result.annualized_return),
        "final_value": float(result.portfolio_values.iloc[-1]),
        "live_mode": False,
        "execution_enabled": False,
        "note": "FinRL-X bt metrics use the engine's own frequency assumptions.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    result = run(job)
    Path(args.result).write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
