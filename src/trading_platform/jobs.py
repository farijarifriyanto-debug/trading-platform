import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True)
class Job:
    job_id: str
    kind: str
    target_id: str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    max_attempts: int
    created_at: str
    updated_at: str
    lease_until: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    idempotency_key: str | None = None


class DurableJobQueue:
    """SQLite durable queue with leases, retry, stale recovery, and idempotency."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lease_until TEXT,
                    error TEXT,
                    result_json TEXT,
                    idempotency_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created
                ON jobs(status, created_at);
                """
            )

    def _row(self, row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"],
            kind=row["kind"],
            target_id=row["target_id"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            lease_until=row["lease_until"],
            error=row["error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            idempotency_key=row["idempotency_key"],
        )

    def enqueue(
        self,
        kind: str,
        target_id: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> Job:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        now = _iso(_now())
        job_id = uuid.uuid4().hex
        with self._lock, self._connect() as conn:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if existing:
                    return self._row(existing)
            conn.execute(
                """
                INSERT INTO jobs(
                    job_id, kind, target_id, payload_json, status, attempts,
                    max_attempts, created_at, updated_at, idempotency_key
                ) VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    target_id,
                    json.dumps(payload or {}, sort_keys=True, separators=(",", ":")),
                    max_attempts,
                    now,
                    now,
                    idempotency_key,
                ),
            )
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row)

    def recover_stale(self) -> int:
        now = _iso(_now())
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status='queued', lease_until=NULL, updated_at=?,
                    error=COALESCE(error, 'worker lease expired; recovered')
                WHERE status='running' AND lease_until IS NOT NULL AND lease_until < ?
                """,
                (now, now),
            )
            return cursor.rowcount

    def claim(self, lease_seconds: int = 120) -> Job | None:
        now_dt = _now()
        now = _iso(now_dt)
        lease = _iso(now_dt + timedelta(seconds=max(10, lease_seconds)))
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE jobs SET status='queued', lease_until=NULL, updated_at=?,
                    error=COALESCE(error, 'worker lease expired; recovered')
                WHERE status='running' AND lease_until IS NOT NULL AND lease_until < ?
                """,
                (now, now),
            )
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, job_id LIMIT 1"
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                """
                UPDATE jobs
                SET status='running', attempts=attempts+1, updated_at=?, lease_until=?, error=NULL
                WHERE job_id=? AND status='queued'
                """,
                (now, lease, row["job_id"]),
            )
            conn.commit()
            claimed = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)
            ).fetchone()
        return self._row(claimed)

    def heartbeat(self, job_id: str, lease_seconds: int = 120) -> Job:
        now_dt = _now()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs SET updated_at=?, lease_until=?
                WHERE job_id=? AND status='running'
                """,
                (_iso(now_dt), _iso(now_dt + timedelta(seconds=max(10, lease_seconds))), job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("job is not running")
        return self.require(job_id)

    def complete(self, job_id: str, result: dict[str, Any] | None = None) -> Job:
        return self._finish(job_id, "completed", result=result)

    def fail(self, job_id: str, error: str) -> Job:
        current = self.require(job_id)
        status: JobStatus = "failed" if current.attempts >= current.max_attempts else "queued"
        return self._finish(job_id, status, error=error)

    def cancel(self, job_id: str) -> Job:
        current = self.require(job_id)
        if current.status in {"completed", "failed"}:
            raise ValueError("terminal job cannot be cancelled")
        return self._finish(job_id, "cancelled")

    def _finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> Job:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status=?, updated_at=?, lease_until=NULL, result_json=?, error=?
                WHERE job_id=?
                """,
                (
                    status,
                    _iso(_now()),
                    json.dumps(result, sort_keys=True, separators=(",", ":")) if result is not None else None,
                    error,
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise FileNotFoundError(job_id)
        return self.require(job_id)

    def get(self, job_id: str) -> Job | None:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("job_id must be a lowercase UUID hex")
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def require(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise FileNotFoundError(job_id)
        return job

    def list(self, limit: int = 100, status: str | None = None) -> list[Job]:
        limit = max(1, min(limit, 1000))
        with self._lock, self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._row(row) for row in rows]

    def counts(self) -> dict[str, int]:
        result = {key: 0 for key in ("queued", "running", "completed", "failed", "cancelled")}
        with self._lock, self._connect() as conn:
            for row in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
                result[row["status"]] = int(row["n"])
        return result
