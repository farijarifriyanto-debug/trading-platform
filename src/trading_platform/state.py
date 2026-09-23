import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from .portfolio import Portfolio


class PortfolioState(Protocol):
    def save_portfolio(self, portfolio: Portfolio) -> None: ...


class RuntimeState:
    """SQLite-backed mutable runtime state. Immutable research artifacts stay file-addressed."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO runtime_meta(key, value) VALUES ('schema_version', '1')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO paper_state(id, cash) VALUES (1, 100000.0)"
            )

    def load_portfolio(self) -> Portfolio:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT cash FROM paper_state WHERE id=1").fetchone()
            positions = {
                item["symbol"]: float(item["quantity"])
                for item in conn.execute(
                    "SELECT symbol, quantity FROM paper_positions WHERE quantity != 0"
                )
            }
        return Portfolio(cash=float(row["cash"]), positions=positions)

    def save_portfolio(self, portfolio: Portfolio) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE paper_state SET cash=? WHERE id=1", (portfolio.cash,))
            conn.execute("DELETE FROM paper_positions")
            conn.executemany(
                "INSERT INTO paper_positions(symbol, quantity) VALUES (?, ?)",
                [
                    (symbol, quantity)
                    for symbol, quantity in sorted(portfolio.positions.items())
                    if quantity != 0
                ],
            )
            conn.commit()

    def health(self) -> dict[str, object]:
        try:
            with self._lock, self._connect() as conn:
                integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
                schema = conn.execute(
                    "SELECT value FROM runtime_meta WHERE key='schema_version'"
                ).fetchone()
            return {
                "ok": integrity == "ok" and schema is not None,
                "integrity": integrity,
                "schema_version": schema["value"] if schema else None,
            }
        except sqlite3.Error as exc:
            return {"ok": False, "integrity": "error", "error": str(exc)}

    def backup(self, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(destination.suffix + ".tmp")
        with self._lock, self._connect() as source, sqlite3.connect(temp) as target:
            source.backup(target)
        temp.replace(destination)
        return destination

    def restore_from(self, source: str | Path) -> None:
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        with sqlite3.connect(source) as conn:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("backup integrity check failed")
        with self._lock:
            temp = self.path.with_suffix(self.path.suffix + ".restore")
            shutil.copy2(source, temp)
            temp.replace(self.path)
            self._initialize()
