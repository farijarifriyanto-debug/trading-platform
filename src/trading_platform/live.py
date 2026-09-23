import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .domain import OrderIntent, Side
from .market import Quote
from .risk import RiskManager, RiskPolicy, RiskRejected


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LiveTradingConfig:
    enabled: bool = False
    exchange_id: str = "kraken"
    allowed_symbols: tuple[str, ...] = ("BTC/USD",)
    max_order_notional: float = 100.0
    max_position_notional: float = 500.0
    max_daily_notional: float = 500.0
    max_quote_age_ms: int = 15_000
    arm_ttl_seconds: int = 300
    one_shot_arm: bool = True

    @classmethod
    def from_env(cls) -> "LiveTradingConfig":
        symbols = tuple(
            item.strip()
            for item in os.getenv("TRADING_LIVE_ALLOWED_SYMBOLS", "BTC/USD").split(",")
            if item.strip()
        )
        config = cls(
            enabled=_truthy(os.getenv("TRADING_LIVE_ENABLED"), False),
            exchange_id=os.getenv("TRADING_LIVE_EXCHANGE", "kraken").strip().lower(),
            allowed_symbols=symbols,
            max_order_notional=float(os.getenv("TRADING_LIVE_MAX_ORDER_NOTIONAL", "100")),
            max_position_notional=float(os.getenv("TRADING_LIVE_MAX_POSITION_NOTIONAL", "500")),
            max_daily_notional=float(os.getenv("TRADING_LIVE_MAX_DAILY_NOTIONAL", "500")),
            max_quote_age_ms=int(os.getenv("TRADING_LIVE_MAX_QUOTE_AGE_MS", "15000")),
            arm_ttl_seconds=int(os.getenv("TRADING_LIVE_ARM_TTL_SECONDS", "300")),
            one_shot_arm=_truthy(os.getenv("TRADING_LIVE_ONE_SHOT_ARM"), True),
        )
        if not config.allowed_symbols:
            raise RuntimeError("TRADING_LIVE_ALLOWED_SYMBOLS must not be empty")
        if min(
            config.max_order_notional,
            config.max_position_notional,
            config.max_daily_notional,
        ) <= 0:
            raise RuntimeError("live notional limits must be positive")
        if config.arm_ttl_seconds < 30 or config.arm_ttl_seconds > 3600:
            raise RuntimeError("TRADING_LIVE_ARM_TTL_SECONDS must be between 30 and 3600")
        if config.enabled and not config.one_shot_arm:
            raise RuntimeError("Phase 8 live execution requires one-shot arming")
        return config


@dataclass(frozen=True)
class LiveOrder:
    request_id: str
    exchange: str
    symbol: str
    side: str
    requested_quantity: float
    quantity: float
    reference_price: float
    notional: float
    status: str
    exchange_order_id: str | None
    client_order_id: str
    created_at: str
    updated_at: str
    error: str | None = None
    raw: dict[str, Any] | None = None


class LiveBroker(Protocol):
    exchange_id: str

    def quote(self, symbol: str) -> Quote: ...

    def position_quantity(self, symbol: str) -> float: ...

    def available_quote_balance(self, symbol: str) -> float: ...

    def available_base_balance(self, symbol: str) -> float: ...

    def normalize_quantity(self, symbol: str, quantity: float) -> float: ...

    def place_market_order(
        self, symbol: str, side: Side, quantity: float, client_order_id: str
    ) -> dict[str, Any]: ...

    def reconciliation_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...

    def cancel_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]: ...


class LiveStateStore:
    """Durable live-control and order ledger. Defaults to disarmed."""

    def __init__(self, path: str | Path, *, disarm_on_start: bool = False):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if disarm_on_start:
            self.disarm()
            self.recover_inflight()

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
                CREATE TABLE IF NOT EXISTS live_control (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    kill_switch INTEGER NOT NULL DEFAULT 1,
                    armed_until TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_orders (
                    request_id TEXT PRIMARY KEY,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    requested_quantity REAL,
                    quantity REAL NOT NULL,
                    reference_price REAL NOT NULL,
                    notional REAL NOT NULL,
                    status TEXT NOT NULL,
                    exchange_order_id TEXT,
                    client_order_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS live_orders_created
                ON live_orders(created_at);
                """
            )
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(live_orders)").fetchall()
            }
            if "requested_quantity" not in columns:
                conn.execute("ALTER TABLE live_orders ADD COLUMN requested_quantity REAL")
                conn.execute("UPDATE live_orders SET requested_quantity=quantity WHERE requested_quantity IS NULL")
            conn.execute(
                """
                INSERT OR IGNORE INTO live_control(id, kill_switch, armed_until, updated_at)
                VALUES (1, 1, NULL, ?)
                """,
                (_iso(_now()),),
            )

    def arm(self, ttl_seconds: int) -> dict[str, Any]:
        ttl_seconds = max(30, min(ttl_seconds, 3600))
        until = _now() + timedelta(seconds=ttl_seconds)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE live_control SET kill_switch=0, armed_until=?, updated_at=? WHERE id=1",
                (_iso(until), _iso(_now())),
            )
        return self.status()

    def disarm(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE live_control SET kill_switch=1, armed_until=NULL, updated_at=? WHERE id=1",
                (_iso(_now()),),
            )
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM live_control WHERE id=1").fetchone()
        armed = False
        if not bool(row["kill_switch"]) and row["armed_until"]:
            try:
                armed = datetime.fromisoformat(row["armed_until"]) > _now()
            except ValueError:
                armed = False
        if not armed and not bool(row["kill_switch"]):
            self.disarm()
            return self.status()
        return {
            "armed": armed,
            "kill_switch": bool(row["kill_switch"]) or not armed,
            "armed_until": row["armed_until"] if armed else None,
            "updated_at": row["updated_at"],
        }

    def reserve(
        self,
        request_id: str,
        exchange: str,
        symbol: str,
        side: Side,
        quantity: float,
        reference_price: float,
        *,
        requested_quantity: float | None = None,
        max_daily_notional: float,
        require_armed: bool = True,
        consume_arm: bool = True,
    ) -> tuple[LiveOrder, bool]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
            raise ValueError("request_id must be 8-64 letters, digits, underscore, or hyphen")
        notional = quantity * reference_price
        client_order_id = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:18]
        now = _iso(_now())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM live_orders WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                conn.commit()
                return self._row(existing), False

            control = conn.execute("SELECT * FROM live_control WHERE id=1").fetchone()
            armed = False
            if not bool(control["kill_switch"]) and control["armed_until"]:
                try:
                    armed = datetime.fromisoformat(control["armed_until"]) > _now()
                except ValueError:
                    armed = False
            if require_armed and not armed:
                conn.rollback()
                raise RiskRejected("live execution is disarmed")

            start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            daily = conn.execute(
                """
                SELECT COALESCE(SUM(notional), 0) AS total
                FROM live_orders
                WHERE created_at >= ?
                  AND status IN ('reserved','submitted','open','closed','unknown')
                """,
                (start,),
            ).fetchone()
            if float(daily["total"]) + notional > max_daily_notional:
                conn.rollback()
                raise RiskRejected("daily live notional limit exceeded")

            conn.execute(
                """
                INSERT INTO live_orders(
                    request_id, exchange, symbol, side, requested_quantity, quantity,
                    reference_price, notional, status, exchange_order_id, client_order_id,
                    created_at, updated_at, error, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reserved', NULL, ?, ?, ?, NULL, NULL)
                """,
                (
                    request_id,
                    exchange,
                    symbol,
                    side.value,
                    requested_quantity if requested_quantity is not None else quantity,
                    quantity,
                    reference_price,
                    notional,
                    client_order_id,
                    now,
                    now,
                ),
            )
            if consume_arm:
                conn.execute(
                    "UPDATE live_control SET kill_switch=1, armed_until=NULL, updated_at=? WHERE id=1",
                    (now,),
                )
            row = conn.execute(
                "SELECT * FROM live_orders WHERE request_id=?", (request_id,)
            ).fetchone()
            conn.commit()
        return self._row(row), True

    def update(
        self,
        request_id: str,
        status: str,
        *,
        exchange_order_id: str | None = None,
        error: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> LiveOrder:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE live_orders
                SET status=?, exchange_order_id=COALESCE(?, exchange_order_id),
                    updated_at=?, error=?, raw_json=?
                WHERE request_id=?
                """,
                (
                    status,
                    exchange_order_id,
                    _iso(_now()),
                    error,
                    json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
                    if raw is not None
                    else None,
                    request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise FileNotFoundError(request_id)
        return self.require(request_id)

    def get(self, request_id: str) -> LiveOrder | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM live_orders WHERE request_id=?", (request_id,)
            ).fetchone()
        return self._row(row) if row else None

    def require(self, request_id: str) -> LiveOrder:
        record = self.get(request_id)
        if record is None:
            raise FileNotFoundError(request_id)
        return record

    def list(self, limit: int = 100) -> list[LiveOrder]:
        limit = max(1, min(limit, 1000))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM live_orders ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(row) for row in rows]

    def recover_inflight(self) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE live_orders
                SET status='unknown', updated_at=?,
                    error=COALESCE(error, 'process restart during live order; reconciliation required')
                WHERE status IN ('reserved','submitted')
                """,
                (_iso(_now()),),
            )
            return cursor.rowcount

    def health(self) -> dict[str, Any]:
        try:
            with self._lock, self._connect() as conn:
                integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            return {"ok": integrity == "ok", "integrity": integrity}
        except sqlite3.Error as exc:
            return {"ok": False, "integrity": "error", "error": str(exc)}

    def daily_reserved_notional(self) -> float:
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(notional), 0) AS total
                FROM live_orders
                WHERE created_at >= ?
                  AND status IN ('reserved','submitted','open','closed','unknown')
                """,
                (start,),
            ).fetchone()
        return float(row["total"])

    def _row(self, row: sqlite3.Row) -> LiveOrder:
        return LiveOrder(
            request_id=row["request_id"],
            exchange=row["exchange"],
            symbol=row["symbol"],
            side=row["side"],
            requested_quantity=float(row["requested_quantity"] if row["requested_quantity"] is not None else row["quantity"]),
            quantity=float(row["quantity"]),
            reference_price=float(row["reference_price"]),
            notional=float(row["notional"]),
            status=row["status"],
            exchange_order_id=row["exchange_order_id"],
            client_order_id=row["client_order_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            error=row["error"],
            raw=json.loads(row["raw_json"]) if row["raw_json"] else None,
        )


class CCXTLiveBroker:
    """Private CCXT boundary. Construction requires credentials; no secret is exposed."""

    def __init__(self, exchange_id: str, exchange: Any | None = None):
        self.exchange_id = exchange_id
        self._balance_cache: dict[str, Any] | None = None
        if exchange is not None:
            self.exchange = exchange
            return
        api_key = os.getenv("TRADING_LIVE_EXCHANGE_API_KEY")
        secret = os.getenv("TRADING_LIVE_EXCHANGE_SECRET")
        if not api_key or not secret:
            raise RuntimeError("live exchange credentials are not configured")
        try:
            import ccxt
        except ImportError as exc:
            raise RuntimeError("CCXT is not installed") from exc
        exchange_cls = getattr(ccxt, exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"unknown CCXT exchange: {exchange_id}")
        self.exchange = exchange_cls(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": os.getenv("TRADING_LIVE_EXCHANGE_PASSWORD") or None,
                "enableRateLimit": True,
            }
        )

    def quote(self, symbol: str) -> Quote:
        raw = self.exchange.fetch_ticker(symbol)
        def number(value):
            try:
                value = float(value)
                return value if value > 0 else None
            except (TypeError, ValueError):
                return None
        return Quote(
            exchange=self.exchange_id,
            symbol=symbol,
            bid=number(raw.get("bid")),
            ask=number(raw.get("ask")),
            last=number(raw.get("last") or raw.get("close")),
            timestamp=raw.get("timestamp") or int(time.time() * 1000),
        )

    def _balance(self) -> dict[str, Any]:
        if self._balance_cache is None:
            self._balance_cache = self.exchange.fetch_balance()
        return self._balance_cache

    def position_quantity(self, symbol: str) -> float:
        market = self.exchange.market(symbol)
        base = market["base"]
        balance = self._balance()
        total = balance.get("total", {}).get(base)
        if total is None:
            total = balance.get(base, {}).get("total", 0)
        return max(0.0, float(total or 0.0))

    def available_quote_balance(self, symbol: str) -> float:
        market = self.exchange.market(symbol)
        quote = market["quote"]
        balance = self._balance()
        free = balance.get("free", {}).get(quote)
        if free is None:
            free = balance.get(quote, {}).get("free", 0)
        return max(0.0, float(free or 0.0))

    def available_base_balance(self, symbol: str) -> float:
        market = self.exchange.market(symbol)
        base = market["base"]
        balance = self._balance()
        free = balance.get("free", {}).get(base)
        if free is None:
            free = balance.get(base, {}).get("free", 0)
        return max(0.0, float(free or 0.0))

    def normalize_quantity(self, symbol: str, quantity: float) -> float:
        if hasattr(self.exchange, "load_markets"):
            self.exchange.load_markets()
        normalized = float(self.exchange.amount_to_precision(symbol, quantity))
        if normalized <= 0:
            raise RiskRejected("quantity rounds to zero at exchange precision")
        market = self.exchange.market(symbol)
        limits = market.get("limits", {}).get("amount", {})
        minimum = limits.get("min")
        maximum = limits.get("max")
        if minimum is not None and normalized < float(minimum):
            raise RiskRejected("quantity is below exchange minimum")
        if maximum is not None and normalized > float(maximum):
            raise RiskRejected("quantity exceeds exchange maximum")
        return normalized

    def place_market_order(
        self, symbol: str, side: Side, quantity: float, client_order_id: str
    ) -> dict[str, Any]:
        return self.exchange.create_order(
            symbol,
            "market",
            side.value,
            quantity,
            None,
            {"clientOrderId": client_order_id},
        )

    def reconciliation_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        since = int((_now() - timedelta(days=1)).timestamp() * 1000)
        open_orders = list(self.exchange.fetch_open_orders(symbol))
        closed_orders = list(self.exchange.fetch_closed_orders(symbol, since=since, limit=100))
        by_key: dict[str, dict[str, Any]] = {}
        for item in open_orders + closed_orders:
            key = str(item.get("id") or item.get("clientOrderId") or len(by_key))
            by_key[key] = item
        return list(by_key.values())

    def cancel_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]:
        result = self.exchange.cancel_order(order_id, symbol)
        return result if isinstance(result, dict) else {"result": result}


class LiveExecutionService:
    def __init__(self, config: LiveTradingConfig, state: LiveStateStore, audit):
        self.config = config
        self.state = state
        self.audit = audit
        self.risk = RiskManager(
            RiskPolicy(
                max_order_notional=config.max_order_notional,
                max_position_notional=config.max_position_notional,
                allow_short=False,
            )
        )

    def status(self) -> dict[str, Any]:
        control = self.state.status()
        return {
            "capability_enabled": self.config.enabled,
            "exchange": self.config.exchange_id,
            "allowed_symbols": list(self.config.allowed_symbols),
            "max_order_notional": self.config.max_order_notional,
            "max_position_notional": self.config.max_position_notional,
            "max_daily_notional": self.config.max_daily_notional,
            "one_shot_arm": self.config.one_shot_arm,
            "daily_reserved_notional": self.state.daily_reserved_notional(),
            **control,
        }

    def submit_market(
        self,
        broker: LiveBroker,
        request_id: str,
        symbol: str,
        side: Side,
        quantity: float,
    ) -> LiveOrder:
        existing = self.state.get(request_id)
        if existing is not None:
            if (
                existing.symbol != symbol
                or existing.side != side.value
                or abs(existing.requested_quantity - quantity) > 1e-12
            ):
                raise RiskRejected("request_id was already used for a different live order")
            return existing
        if not self.config.enabled:
            raise RiskRejected("live execution capability is disabled")
        control = self.state.status()
        if not control["armed"]:
            raise RiskRejected("live execution is disarmed")
        if broker.exchange_id != self.config.exchange_id:
            raise RiskRejected("exchange is not the configured live exchange")
        if symbol not in self.config.allowed_symbols:
            raise RiskRejected("symbol is not allowlisted")
        if quantity <= 0:
            raise RiskRejected("quantity must be positive")

        requested_quantity = quantity
        quantity = broker.normalize_quantity(symbol, quantity)
        quote = broker.quote(symbol)
        now_ms = int(time.time() * 1000)
        if quote.timestamp is None:
            raise RiskRejected("live quote has no freshness timestamp")
        if abs(now_ms - int(quote.timestamp)) > self.config.max_quote_age_ms:
            raise RiskRejected("live quote is stale")
        price = quote.executable_price(side)
        notional = price * quantity

        current_position = broker.position_quantity(symbol)
        self.risk.validate(OrderIntent(symbol, side, quantity, price), current_position)
        if side == Side.BUY:
            available_quote = broker.available_quote_balance(symbol)
            if notional * 1.01 > available_quote:
                raise RiskRejected("insufficient available quote balance with safety buffer")
        else:
            available_base = broker.available_base_balance(symbol)
            if quantity > available_base:
                raise RiskRejected("insufficient available base balance")
        record, created = self.state.reserve(
            request_id,
            broker.exchange_id,
            symbol,
            side,
            quantity,
            price,
            requested_quantity=requested_quantity,
            max_daily_notional=self.config.max_daily_notional,
            require_armed=True,
            consume_arm=self.config.one_shot_arm,
        )
        if not created:
            return record

        self.audit.record(
            "live_order_reserved",
            {
                "request_id": request_id,
                "exchange": broker.exchange_id,
                "symbol": symbol,
                "side": side.value,
                "quantity": quantity,
                "reference_price": price,
                "notional": notional,
            },
        )
        try:
            raw = broker.place_market_order(symbol, side, quantity, record.client_order_id)
            exchange_order_id = str(raw.get("id")) if raw.get("id") is not None else None
            status = str(raw.get("status") or "submitted")
            record = self.state.update(
                request_id,
                status,
                exchange_order_id=exchange_order_id,
                raw=raw,
            )
            self.audit.record(
                "live_order_submitted",
                {
                    "request_id": request_id,
                    "exchange_order_id": exchange_order_id,
                    "status": status,
                },
            )
            return record
        except Exception as exc:
            record = self.state.update(
                request_id,
                "unknown",
                error=str(exc)[-1000:],
            )
            self.audit.record(
                "live_order_unknown",
                {
                    "request_id": request_id,
                    "error": str(exc)[-1000:],
                    "manual_reconciliation_required": True,
                },
            )
            raise RuntimeError(
                "live order outcome is unknown; do not retry this request_id until reconciled"
            ) from exc

    def reconcile_orders(self, broker: LiveBroker) -> dict[str, Any]:
        if broker.exchange_id != self.config.exchange_id:
            raise RiskRejected("exchange is not the configured live exchange")
        remote = broker.reconciliation_orders()
        by_client = {
            str(item.get("clientOrderId")): item
            for item in remote
            if item.get("clientOrderId")
        }
        updated = 0
        for record in self.state.list(limit=1000):
            raw = by_client.get(record.client_order_id)
            if raw is None:
                continue
            exchange_order_id = str(raw.get("id")) if raw.get("id") is not None else None
            status = str(raw.get("status") or "open")
            self.state.update(
                record.request_id,
                status,
                exchange_order_id=exchange_order_id,
                raw=raw,
            )
            updated += 1
        self.audit.record(
            "live_reconciliation",
            {"exchange": broker.exchange_id, "remote_orders": len(remote), "updated": updated},
        )
        unresolved = sum(1 for record in self.state.list(limit=1000) if record.status == "unknown")
        return {"remote_orders": len(remote), "updated": updated, "unresolved_unknown": unresolved}

    def emergency_stop(self, broker: LiveBroker) -> dict[str, Any]:
        self.state.disarm()
        cancelled = 0
        errors: list[str] = []
        local_client_ids = {record.client_order_id for record in self.state.list(limit=1000)}
        try:
            remote = broker.reconciliation_orders()
        except Exception as exc:
            remote = []
            errors.append(f"reconciliation: {str(exc)[-500:]}")
        for item in remote:
            client_id = str(item.get("clientOrderId") or "")
            order_id = item.get("id")
            symbol = item.get("symbol")
            status = str(item.get("status") or "").lower()
            if (
                client_id not in local_client_ids
                or not order_id
                or symbol not in self.config.allowed_symbols
                or status not in {"open", "new", "pending"}
            ):
                continue
            try:
                broker.cancel_order(str(order_id), str(symbol))
                cancelled += 1
            except Exception as exc:
                errors.append(f"{symbol}/{order_id}: {str(exc)[-500:]}")
        self.audit.record(
            "live_emergency_stop",
            {
                "exchange": broker.exchange_id,
                "cancelled": cancelled,
                "errors": errors,
                "kill_switch": True,
            },
        )
        return {
            "kill_switch": True,
            "armed": False,
            "cancelled": cancelled,
            "errors": errors,
        }
