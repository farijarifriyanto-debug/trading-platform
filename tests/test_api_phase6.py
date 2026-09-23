from fastapi.testclient import TestClient

from trading_platform.api import app, dataset_store
from trading_platform.market import Candle

client = TestClient(app)


def _dataset():
    return dataset_store.save(
        "fake",
        "BTC/USD",
        "1h",
        [
            Candle(1_700_000_000_000 + i * 3_600_000, 100 + i, 101 + i, 99 + i, 100 + i, 10 + i)
            for i in range(40)
        ],
    )


def test_ai_health_is_research_only():
    response = client.get("/ai/health")
    assert response.status_code == 200
    body = response.json()
    assert body["execution_enabled"] is False
    assert body["live_mode"] is False
    assert body["model_family"] == "random_forest_direction"


def test_ai_candidate_creation_records_dataset_and_policy():
    dataset = _dataset()
    response = client.post(
        "/ai/candidates",
        json={
            "dataset_id": dataset.dataset_id,
            "seed": 7,
            "folds": 3,
            "hyperparameters": {"n_estimators": 50},
            "robustness": {
                "min_samples": 30,
                "min_folds": 3,
                "min_mean_accuracy": 0.5,
                "min_accuracy_uplift": 0.0,
                "max_accuracy_std": 0.2,
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "planned"
    assert body["spec"]["dataset_id"] == dataset.dataset_id
    assert body["spec"]["seed"] == 7
    assert body["gate_status"] is None


def test_finrlx_health_never_exposes_execution():
    response = client.get("/ai/finrlx/health")
    assert response.status_code == 200
    body = response.json()
    assert body["execution_enabled"] is False
    assert body["live_mode"] is False
