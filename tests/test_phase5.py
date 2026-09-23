import json
from dataclasses import asdict

import pytest

from trading_platform.datasets import HistoricalDatasetStore
from trading_platform.experiments import ExperimentSpec, ExperimentStore
from trading_platform.lean_export import LeanExporter
from trading_platform.market import Candle
from trading_platform.workers import SimulationWorkerService


def _dataset(tmp_path):
    return HistoricalDatasetStore(tmp_path / "historical").save(
        "fake",
        "BTC/USD",
        "1h",
        [
            Candle(1_700_000_000_000, 100, 101, 99, 100, 10),
            Candle(1_700_003_600_000, 99, 100, 98, 99, 10),
            Candle(1_700_007_200_000, 98, 99, 97, 98, 10),
            Candle(1_700_010_800_000, 99, 100, 98, 99, 10),
            Candle(1_700_014_400_000, 101, 102, 100, 101, 10),
            Candle(1_700_018_000_000, 103, 104, 102, 103, 10),
        ],
    )


def test_experiment_id_is_reproducible_and_store_tracks_status(tmp_path):
    store = ExperimentStore(tmp_path / "experiments")
    spec = ExperimentSpec(
        dataset_id="a" * 64,
        engine="native",
        strategy="sma_trend",
        parameters={"fast": 2, "slow": 3},
    )

    first = store.create(spec)
    second = store.create(spec)
    completed = store.update(
        first.experiment_id,
        "completed",
        result={"start_cash": 100, "end_equity": 101, "return_fraction": 0.01, "trades": 2},
    )

    assert first.experiment_id == second.experiment_id
    assert completed.status == "completed"
    assert store.require(first.experiment_id).result["end_equity"] == 101
    assert store.compare()[0]["engine"] == "native"


def test_experiment_store_rejects_path_traversal(tmp_path):
    store = ExperimentStore(tmp_path / "experiments")
    with pytest.raises(ValueError, match="SHA-256"):
        store.get("../../etc/passwd")


def test_native_worker_completes_experiment_without_external_engine(tmp_path):
    dataset = _dataset(tmp_path)
    store = ExperimentStore(tmp_path / "experiments")
    record = store.create(
        ExperimentSpec(
            dataset_id=dataset.dataset_id,
            engine="native",
            strategy="sma_trend",
            parameters={"fast": 2, "slow": 3},
            quantity=1,
        )
    )
    worker = SimulationWorkerService(
        store,
        nautilus_python="/missing/python",
        nautilus_script="/missing/worker.py",
    )

    completed = worker.run(record, dataset)

    assert completed.status == "completed"
    assert completed.result["engine"] == "native"
    assert completed.result["live_mode"] is False


def test_lean_export_is_deterministic_and_live_off(tmp_path):
    dataset = _dataset(tmp_path)
    exporter = LeanExporter(tmp_path / "lean")

    first = exporter.export_sma(dataset, {"fast": 2, "slow": 3})
    second = exporter.export_sma(dataset, {"fast": 2, "slow": 3})

    assert first.export_id == second.export_id
    assert first.live_mode is False
    manifest = json.loads((tmp_path / "lean" / first.export_id / "manifest.json").read_text())
    config = json.loads((tmp_path / "lean" / first.export_id / "config.json").read_text())
    main = (tmp_path / "lean" / first.export_id / "main.py").read_text()
    assert manifest["live_mode"] is False
    assert config["live-mode"] is False
    assert "TradingPlatformSMA" in main
    assert "set_holdings" in main


def test_worker_health_never_advertises_lean_live(tmp_path):
    store = ExperimentStore(tmp_path / "experiments")
    worker = SimulationWorkerService(
        store,
        nautilus_python="/missing/python",
        nautilus_script="/missing/worker.py",
    )

    health = worker.health()

    assert health["native"]["available"] is True
    assert health["nautilus"]["available"] is False
    assert health["lean"]["live_mode"] is False
