from dataclasses import asdict

import pytest

from trading_platform.datasets import HistoricalDatasetStore
from trading_platform.market import Candle
from trading_platform.simulation import (
    LeanSimulationAdapter,
    NautilusSimulationAdapter,
    SimulationPlanStore,
    SimulationSpec,
    default_simulation_registry,
)


def _dataset(tmp_path):
    store = HistoricalDatasetStore(tmp_path / "historical")
    return store.save(
        "kraken",
        "BTC/USD",
        "1h",
        [
            Candle(1, 100, 101, 99, 100, 10),
            Candle(2, 101, 102, 100, 101, 11),
        ],
    )


def _spec(dataset, engine):
    return SimulationSpec(
        engine=engine,
        dataset_id=dataset.dataset_id,
        symbol=dataset.symbol,
        strategy="sma_trend",
        parameters={"fast": 5, "slow": 20},
    )


def test_nautilus_plan_is_backtest_only_and_deterministic(tmp_path):
    dataset = _dataset(tmp_path)
    adapter = NautilusSimulationAdapter()

    first = adapter.plan(dataset, _spec(dataset, "nautilus"))
    second = adapter.plan(dataset, _spec(dataset, "nautilus"))

    assert first == second
    assert first.live_mode is False
    assert first.manifest["execution_enabled"] is False
    assert first.manifest["engine_config"]["shutdown_on_error"] is True
    assert first.manifest["venue"]["fee_model"] == "explicit_zero_baseline"


def test_lean_plan_hard_disables_live_mode(tmp_path):
    dataset = _dataset(tmp_path)
    plan = LeanSimulationAdapter().plan(dataset, _spec(dataset, "lean"))

    assert plan.live_mode is False
    assert plan.manifest["lean_config"]["live-mode"] is False
    assert plan.manifest["execution_enabled"] is False
    assert plan.manifest["dataset"]["dataset_id"] == dataset.dataset_id


def test_simulation_registry_contains_both_boundaries():
    catalog = default_simulation_registry().catalog()

    assert [item["engine"] for item in catalog] == ["lean", "nautilus"]
    assert all(item["execution_enabled"] is False for item in catalog)


def test_plan_store_persists_and_integrity_checks(tmp_path):
    dataset = _dataset(tmp_path)
    plan = NautilusSimulationAdapter().plan(dataset, _spec(dataset, "nautilus"))
    store = SimulationPlanStore(tmp_path / "plans")

    store.save(plan)
    loaded = store.load(plan.plan_id)

    assert loaded == plan
    assert store.list()[0]["plan_id"] == plan.plan_id

    path = tmp_path / "plans" / f"{plan.plan_id}.json"
    import json

    record = json.loads(path.read_text())
    record["manifest"]["execution_enabled"] = True
    path.write_text(json.dumps(record))

    with pytest.raises(ValueError, match="integrity"):
        store.load(plan.plan_id)


def test_plan_id_rejects_path_traversal(tmp_path):
    store = SimulationPlanStore(tmp_path / "plans")
    with pytest.raises(ValueError, match="SHA-256"):
        store.load("../../etc/passwd")
