from fastapi.testclient import TestClient

from trading_platform.api import app

client = TestClient(app)


def test_dashboard_is_served_and_marks_live_off():
    response = client.get("/")
    assert response.status_code == 200
    assert "Trading Platform" in response.text
    assert "LIVE OFF" in response.text


def test_metrics_endpoint_has_control_plane_metrics():
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["dataset_count"] >= 0
    assert body["paper_cash"] >= 0
    assert body["open_positions"] >= 0


def test_simulation_engine_catalog_is_non_executing():
    response = client.get("/simulations/engines")
    assert response.status_code == 200
    body = response.json()
    assert [item["engine"] for item in body] == ["lean", "nautilus"]
    assert all(item["execution_enabled"] is False for item in body)


def test_simulation_plan_api_builds_nautilus_boundary_without_execution():
    from trading_platform.api import dataset_store
    from trading_platform.market import Candle

    dataset = dataset_store.save(
        "fake",
        "BTC/USD",
        "1h",
        [
            Candle(1, 100, 101, 99, 100, 1),
            Candle(2, 101, 102, 100, 101, 1),
        ],
    )
    response = client.post(
        "/simulations/nautilus/plans",
        json={
            "dataset_id": dataset.dataset_id,
            "strategy": "sma_trend",
            "parameters": {"fast": 5, "slow": 20},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "nautilus"
    assert body["live_mode"] is False
    assert body["manifest"]["execution_enabled"] is False
    assert body["dataset_id"] == dataset.dataset_id
