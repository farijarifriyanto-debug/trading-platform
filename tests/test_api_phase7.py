from fastapi.testclient import TestClient

from trading_platform.api import app

client = TestClient(app)


def test_phase7_health_and_readiness():
    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json()["version"] == "0.7.0"
    assert health.json()["live_trading"] is False
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert ready.json()["runtime_state"]["ok"] is True


def test_security_status_and_headers():
    response = client.get("/security/status")

    assert response.status_code == 200
    assert response.json()["live_trading"] is False
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_prometheus_metrics_include_job_queue_and_paper_mode():
    response = client.get("/metrics/prometheus")

    assert response.status_code == 200
    assert 'trading_platform_info{mode="paper",live_trading="false"} 1' in response.text
    assert 'trading_platform_jobs{status="queued"}' in response.text


def test_jobs_api_lists_durable_jobs():
    response = client.get("/jobs")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
