import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class AuditSink(Protocol):
    def record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def _event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }


class JSONLAuditLog:
    """Append-only paper-trading audit log."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = _event(event_type, payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        return event

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0 or not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:] if line.strip()]


class MemoryAuditLog:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = _event(event_type, payload)
        self.events.append(event)
        return event

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.events[-limit:] if limit > 0 else []
