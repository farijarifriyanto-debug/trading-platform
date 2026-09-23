from fastapi.testclient import TestClient

from trading_platform.api import app

client = TestClient(app)


def test_phase8_live_capability_is_disabled_and_disarmed_by_default():
    status = client.get("/live/status")

    assert status.status_code == 200
    body = status.json()
    assert body["capability_enabled"] is False
    assert body["armed"] is False
    assert body["kill_switch"] is True


def test_phase8_live_order_cannot_run_when_capability_disabled():
    response = client.post(
        "/live/orders",
        json={
            "request_id": "api_request_001",
            "symbol": "BTC/USD",
            "side": "buy",
            "quantity": 0.001,
        },
    )

    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]


def test_phase8_has_no_http_arm_endpoint():
    response = client.post("/live/arm", json={"ttl": 60})

    assert response.status_code == 404
