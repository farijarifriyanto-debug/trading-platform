import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .datasets import HistoricalDataset


@dataclass(frozen=True)
class LeanExport:
    export_id: str
    path: str
    dataset_id: str
    strategy: str
    live_mode: bool
    files: tuple[str, ...]


class LeanExporter:
    """Creates a deterministic LEAN CLI project bundle. It never invokes lean live."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def export_sma(
        self,
        dataset: HistoricalDataset,
        parameters: dict[str, Any],
        initial_cash: float = 100_000.0,
    ) -> LeanExport:
        fast = int(parameters["fast"])
        slow = int(parameters["slow"])
        if fast <= 0 or slow <= fast:
            raise ValueError("require 0 < fast < slow")
        manifest = {
            "schema_version": 1,
            "engine": "lean",
            "mode": "backtest",
            "dataset_id": dataset.dataset_id,
            "symbol": dataset.symbol,
            "timeframe": dataset.timeframe,
            "strategy": "sma_trend",
            "parameters": {"fast": fast, "slow": slow},
            "initial_cash": initial_cash,
            "live_mode": False,
        }
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
        export_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        target = self.root / export_id
        target.mkdir(parents=True, exist_ok=True)

        data_name = f"{dataset.dataset_id}.csv"
        data_path = target / data_name
        with data_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for candle in dataset.candles:
                timestamp = datetime.fromtimestamp(
                    candle.timestamp / 1000,
                    tz=timezone.utc,
                ).isoformat()
                writer.writerow(
                    [
                        timestamp,
                        candle.open,
                        candle.high,
                        candle.low,
                        candle.close,
                        candle.volume,
                    ]
                )

        main_py = self._algorithm(
            data_name=data_name,
            symbol=dataset.symbol,
            fast=fast,
            slow=slow,
            initial_cash=initial_cash,
            first_ts=dataset.first_timestamp,
            last_ts=dataset.last_timestamp,
        )
        (target / "main.py").write_text(main_py, encoding="utf-8")
        (target / "config.json").write_text(
            json.dumps(
                {
                    "algorithm-language": "Python",
                    "algorithm-type-name": "TradingPlatformSMA",
                    "live-mode": False,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (target / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return LeanExport(
            export_id=export_id,
            path=str(target),
            dataset_id=dataset.dataset_id,
            strategy="sma_trend",
            live_mode=False,
            files=("main.py", "config.json", "manifest.json", data_name),
        )

    @staticmethod
    def _algorithm(
        data_name: str,
        symbol: str,
        fast: int,
        slow: int,
        initial_cash: float,
        first_ts: int | None,
        last_ts: int | None,
    ) -> str:
        if first_ts is None or last_ts is None:
            raise ValueError("dataset must not be empty")
        start = datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc)
        return f'''from AlgorithmImports import *
import os
from datetime import datetime, timedelta


class TradingPlatformBar(PythonData):
    def get_source(self, config, date, is_live_mode):
        source = os.path.join(Globals.data_folder, "{data_name}")
        return SubscriptionDataSource(
            source,
            SubscriptionTransportMedium.LOCAL_FILE,
            FileFormat.CSV,
        )

    def reader(self, config, line, date, is_live_mode):
        if not line or line.startswith("timestamp"):
            return None
        row = line.split(",")
        bar = TradingPlatformBar()
        bar.symbol = config.symbol
        bar.time = datetime.fromisoformat(row[0].replace("Z", "+00:00")).replace(tzinfo=None)
        bar.end_time = bar.time + timedelta(minutes=1)
        bar.value = float(row[4])
        bar["open"] = float(row[1])
        bar["high"] = float(row[2])
        bar["low"] = float(row[3])
        bar["close"] = float(row[4])
        bar["volume"] = float(row[5])
        return bar


class TradingPlatformSMA(QCAlgorithm):
    def initialize(self):
        self.set_start_date({start.year}, {start.month}, {start.day})
        self.set_end_date({end.year}, {end.month}, {end.day})
        self.set_cash({initial_cash!r})
        self.asset = self.add_data(TradingPlatformBar, "{symbol}", Resolution.MINUTE).symbol
        self.fast = SimpleMovingAverage({fast})
        self.slow = SimpleMovingAverage({slow})

    def on_data(self, data):
        if not data.contains_key(self.asset):
            return
        price = data[self.asset].value
        self.fast.update(self.time, price)
        self.slow.update(self.time, price)
        if not self.slow.is_ready:
            return
        if self.fast.current.value > self.slow.current.value and not self.portfolio.invested:
            self.set_holdings(self.asset, 0.01)
        elif self.fast.current.value < self.slow.current.value and self.portfolio.invested:
            self.liquidate(self.asset)
'''
