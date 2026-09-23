import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_platform.backup import create_backup, restore_backup, verify_backup
from trading_platform.domain import OrderIntent, Side
from trading_platform.execution import PaperBroker
from trading_platform.jobs import DurableJobQueue
from trading_platform.security import SecurityConfig, install_security_middleware
from trading_platform.state import RuntimeState


def test_paper_portfolio_survives_broker_restart(tmp_path):
    state = RuntimeState(tmp_path / "runtime.sqlite3")
    broker = PaperBroker(portfolio=state.load_portfolio(), state=state)
    broker.submit(OrderIntent("BTC/USD", Side.BUY, 1, 100))

    restarted = PaperBroker(portfolio=RuntimeState(tmp_path / "runtime.sqlite3").load_portfolio())

    assert restarted.portfolio.position("BTC/USD") == 1
    assert restarted.portfolio.cash < 100_000


def test_runtime_state_backup_and_restore(tmp_path):
    state = RuntimeState(tmp_path / "data" / "runtime.sqlite3")
    broker = PaperBroker(portfolio=state.load_portfolio(), state=state)
    broker.submit(OrderIntent("ETH/USD", Side.BUY, 2, 50))

    archive = create_backup(tmp_path / "data", tmp_path / "backup.tar.gz")
    verified = verify_backup(archive)
    restore_backup(archive, tmp_path / "restored")

    restored = RuntimeState(tmp_path / "restored" / "runtime.sqlite3").load_portfolio()
    assert verified["ok"] is True
    assert restored.position("ETH/USD") == 2
    assert restored.cash == pytest.approx(broker.portfolio.cash)


def test_durable_queue_idempotency_retry_and_completion(tmp_path):
    queue = DurableJobQueue(tmp_path / "jobs.sqlite3")
    first = queue.enqueue("experiment.run", "abc", idempotency_key="same", max_attempts=2)
    duplicate = queue.enqueue("experiment.run", "abc", idempotency_key="same", max_attempts=2)
    assert first.job_id == duplicate.job_id

    claimed = queue.claim(lease_seconds=30)
    assert claimed.status == "running"
    assert claimed.attempts == 1

    retried = queue.fail(claimed.job_id, "temporary")
    assert retried.status == "queued"

    claimed_again = queue.claim(lease_seconds=30)
    assert claimed_again.attempts == 2
    completed = queue.complete(claimed_again.job_id, {"ok": True})
    assert completed.status == "completed"
    assert completed.result == {"ok": True}


def test_durable_queue_recovers_expired_worker_lease(tmp_path):
    queue = DurableJobQueue(tmp_path / "jobs.sqlite3")
    job = queue.enqueue("ai.run", "candidate")
    claimed = queue.claim(lease_seconds=30)

    expired = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    with sqlite3.connect(queue.path) as conn:
        conn.execute("UPDATE jobs SET lease_until=? WHERE job_id=?", (expired, claimed.job_id))

    recovered = queue.recover_stale()
    assert recovered == 1
    assert queue.require(job.job_id).status == "queued"


def test_mutation_auth_and_security_headers():
    app = FastAPI()
    install_security_middleware(
        app,
        SecurityConfig(
            require_auth=True,
            api_key="test-secret",
            max_body_bytes=1024,
            mutation_rate_per_minute=10,
        ),
    )

    @app.get("/read")
    def read():
        return {"ok": True}

    @app.post("/write")
    def write():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/read").status_code == 200
    assert client.post("/write").status_code == 401
    authorized = client.post("/write", headers={"Authorization": "Bearer test-secret"})
    assert authorized.status_code == 200
    assert authorized.headers["x-content-type-options"] == "nosniff"
    assert authorized.headers["x-frame-options"] == "DENY"


def test_request_body_limit():
    app = FastAPI()
    install_security_middleware(
        app,
        SecurityConfig(
            require_auth=False,
            api_key=None,
            max_body_bytes=10,
            mutation_rate_per_minute=10,
        ),
    )

    @app.post("/write")
    def write():
        return {"ok": True}

    response = TestClient(app).post("/write", content=b"x" * 11)
    assert response.status_code == 413


def test_paper_broker_rolls_back_memory_if_persistence_fails():
    class BrokenState:
        def save_portfolio(self, portfolio):
            raise OSError("disk full")

    broker = PaperBroker(state=BrokenState())
    before_cash = broker.portfolio.cash

    with pytest.raises(OSError, match="disk full"):
        broker.submit(OrderIntent("BTC/USD", Side.BUY, 1, 100))

    assert broker.portfolio.cash == before_cash
    assert broker.portfolio.position("BTC/USD") == 0
