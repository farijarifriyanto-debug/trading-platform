from fastapi.testclient import TestClient

from trading_platform.api import app, dataset_store
from trading_platform.market import Candle

client = TestClient(app)


def _api_dataset():
    return dataset_store.save(
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


def test_worker_health_endpoint_is_safe():
    response = client.get("/workers/health")
    assert response.status_code == 200
    body = response.json()
    assert body["native"]["available"] is True
    assert body["lean"]["live_mode"] is False


def test_native_experiment_runs_end_to_end():
    dataset = _api_dataset()
    created = client.post(
        "/experiments",
        json={
            "dataset_id": dataset.dataset_id,
            "engine": "native",
            "strategy": "sma_trend",
            "parameters": {"fast": 2, "slow": 3},
            "quantity": 1,
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["experiment_id"]

    completed = client.post(f"/experiments/{experiment_id}/run")

    assert completed.status_code == 200
    body = completed.json()
    assert body["status"] == "completed"
    assert body["result"]["engine"] == "native"
    assert body["result"]["live_mode"] is False


def test_lean_export_endpoint_produces_backtest_only_bundle():
    dataset = _api_dataset()
    response = client.post(
        "/lean/exports",
        json={
            "dataset_id": dataset.dataset_id,
            "parameters": {"fast": 2, "slow": 3},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["live_mode"] is False
    assert "main.py" in body["files"]
    assert "manifest.json" in body["files"]


def test_experiment_compare_lists_completed_native_result():
    response = client.get("/experiments/compare")
    assert response.status_code == 200
    rows = response.json()
    assert any(row["engine"] == "native" for row in rows)
