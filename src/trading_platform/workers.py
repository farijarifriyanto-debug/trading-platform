import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .backtest import run_sma_backtest
from .datasets import HistoricalDataset
from .experiments import ExperimentRecord, ExperimentStore
from .research import VectorBTResearch, VectorBTUnavailable


class WorkerUnavailable(RuntimeError):
    pass


class WorkerFailed(RuntimeError):
    pass


class SimulationWorkerService:
    def __init__(
        self,
        experiments: ExperimentStore,
        nautilus_python: str | None = None,
        nautilus_script: str | Path | None = None,
        timeout_seconds: int = 120,
    ):
        self.experiments = experiments
        self.nautilus_python = nautilus_python or os.getenv(
            "NAUTILUS_WORKER_PYTHON",
            "/home/botadmin/.venvs/trading-nautilus/bin/python",
        )
        self.nautilus_script = Path(
            nautilus_script
            or os.getenv(
                "NAUTILUS_WORKER_SCRIPT",
                str(Path(__file__).resolve().parents[2] / "workers" / "nautilus_worker.py"),
            )
        )
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict[str, Any]:
        nautilus_available = Path(self.nautilus_python).is_file() and self.nautilus_script.is_file()
        lean_cli = os.getenv("LEAN_CLI", "/home/botadmin/.venvs/trading-lean/bin/lean")
        return {
            "native": {"available": True, "isolated": False},
            "vectorbt": {
                "available": importlib.util.find_spec("vectorbt") is not None,
                "isolated": False,
                "live_mode": False,
            },
            "nautilus": {
                "available": nautilus_available,
                "isolated": True,
                "python": self.nautilus_python if nautilus_available else None,
            },
            "lean": {
                "available": Path(lean_cli).is_file(),
                "mode": "export",
                "live_mode": False,
            },
        }

    def run(self, record: ExperimentRecord, dataset: HistoricalDataset) -> ExperimentRecord:
        if record.spec.strategy != "sma_trend":
            raise WorkerFailed(f"unsupported strategy: {record.spec.strategy}")
        self.experiments.update(record.experiment_id, "running")
        try:
            if record.spec.engine == "native":
                result = self._run_native(record, dataset)
            elif record.spec.engine == "vectorbt":
                result = self._run_vectorbt(record, dataset)
            elif record.spec.engine == "nautilus":
                result = self._run_nautilus(record, dataset)
            else:
                raise WorkerFailed(f"engine is not executable by this worker: {record.spec.engine}")
        except Exception as exc:
            self.experiments.update(record.experiment_id, "failed", error=str(exc))
            raise
        return self.experiments.update(record.experiment_id, "completed", result=result)

    def _run_native(self, record: ExperimentRecord, dataset: HistoricalDataset) -> dict[str, Any]:
        parameters = record.spec.parameters
        prices = [candle.close for candle in dataset.candles]
        quantity = record.spec.quantity
        if quantity is None:
            quantity = record.spec.initial_cash * 0.01 / prices[0]
        result = run_sma_backtest(
            prices,
            symbol=dataset.symbol,
            quantity=quantity,
            fast=int(parameters["fast"]),
            slow=int(parameters["slow"]),
            initial_cash=record.spec.initial_cash,
        )
        return {
            "engine": "native",
            "engine_version": "0.5.0",
            "dataset_id": dataset.dataset_id,
            "symbol": dataset.symbol,
            "strategy": record.spec.strategy,
            "parameters": parameters,
            "start_cash": result.start_cash,
            "end_equity": result.end_equity,
            "return_fraction": (result.end_equity - result.start_cash) / result.start_cash,
            "trades": result.trades,
            "live_mode": False,
        }


    def _run_vectorbt(self, record: ExperimentRecord, dataset: HistoricalDataset) -> dict[str, Any]:
        parameters = record.spec.parameters
        prices = [candle.close for candle in dataset.candles]
        quantity = record.spec.quantity
        if quantity is None:
            quantity = record.spec.initial_cash * 0.01 / prices[0]
        try:
            result = VectorBTResearch().sma_cross(
                prices,
                fast=int(parameters["fast"]),
                slow=int(parameters["slow"]),
                initial_cash=record.spec.initial_cash,
                fees=0.0005,
                quantity=quantity,
            )
        except VectorBTUnavailable as exc:
            raise WorkerUnavailable(str(exc)) from exc
        return {
            "engine": "vectorbt",
            "engine_version": "0.28.x",
            "dataset_id": dataset.dataset_id,
            "symbol": dataset.symbol,
            "strategy": record.spec.strategy,
            "parameters": parameters,
            "start_cash": result.initial_cash,
            "end_equity": result.final_value,
            "return_fraction": result.total_return,
            "trades": result.trades,
            "live_mode": False,
        }

    def _run_nautilus(self, record: ExperimentRecord, dataset: HistoricalDataset) -> dict[str, Any]:
        if not Path(self.nautilus_python).is_file() or not self.nautilus_script.is_file():
            raise WorkerUnavailable("Nautilus worker environment is not installed")
        job = {
            "dataset_id": dataset.dataset_id,
            "symbol": dataset.symbol,
            "timeframe": dataset.timeframe,
            "candles": [asdict(candle) for candle in dataset.candles],
            "parameters": record.spec.parameters,
            "initial_cash": record.spec.initial_cash,
            "quantity": record.spec.quantity,
        }
        with tempfile.TemporaryDirectory(prefix="trading-nautilus-") as temp:
            job_path = Path(temp) / "job.json"
            result_path = Path(temp) / "result.json"
            job_path.write_text(json.dumps(job, separators=(",", ":")), encoding="utf-8")
            completed = subprocess.run(
                [
                    self.nautilus_python,
                    str(self.nautilus_script),
                    "--job",
                    str(job_path),
                    "--result",
                    str(result_path),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                tail = (completed.stderr or completed.stdout)[-2000:]
                raise WorkerFailed(f"Nautilus worker failed ({completed.returncode}): {tail}")
            if not result_path.exists():
                raise WorkerFailed("Nautilus worker did not produce a result")
            result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("live_mode") is not False:
            raise WorkerFailed("Nautilus worker violated live-mode safety gate")
        return result
