from fastapi.testclient import TestClient

from trading_platform.api import app

client = TestClient(app)


def test_health_reports_paper_only_v06():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "mode": "paper",
        "live_trading": False,
        "version": "0.6.0",
    }


def test_strategy_catalog_exposes_builtin_registry():
    response = client.get("/strategies")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert "sma_trend" in names


def test_sweep_api_accepts_inline_prices_without_market_network():
    response = client.post(
        "/research/sweeps/sma",
        json={
            "prices": [10, 10, 9, 8, 7, 8, 9, 10, 11, 12],
            "fast_values": [2, 3],
            "slow_values": [4, 8],
            "quantity": 1,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dataset_id"] is None
    assert body["evaluated"] == 4
    assert len(body["results"]) == 4


def test_sweep_api_requires_exactly_one_data_source():
    response = client.post(
        "/research/sweeps/sma",
        json={
            "prices": [1, 2, 3, 4],
            "dataset_id": "a" * 64,
            "fast_values": [1],
            "slow_values": [2],
        },
    )
    assert response.status_code == 422
    assert "exactly one" in response.json()["detail"]
