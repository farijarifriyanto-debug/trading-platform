import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_platform.audit import MemoryAuditLog
from trading_platform.domain import Side
from trading_platform.live import LiveExecutionService, LiveStateStore, LiveTradingConfig
from trading_platform.market import Quote
from trading_platform.risk import RiskRejected
from trading_platform.security import SecurityConfig, install_security_middleware


class FakeLiveBroker:
    exchange_id = "kraken"
    market_type = "spot"

    def __init__(
        self,
        *,
        price=50.0,
        position=0.0,
        quote_balance=10_000.0,
        timestamp=None,
        fail_submit=False,
        hedged=False,
        minimum_cost=None,
    ):
        self.price = price
        self.position = position
        self.quote_balance = quote_balance
        self.timestamp = timestamp if timestamp is not None else int(time.time() * 1000)
        self.fail_submit = fail_submit
        self.hedged = hedged
        self._minimum_cost = minimum_cost
        self.orders = []
        self.remote_open = []
        self.reconciliation_symbols = []

    def is_contract(self, symbol):
        return self.market_type != "spot"

    def position_mode_hedged(self):
        return self.hedged

    def quote(self, symbol):
        return Quote(self.exchange_id, symbol, self.price - 1, self.price, self.price, self.timestamp)

    def position_quantity(self, symbol):
        return self.position

    def available_quote_balance(self, symbol):
        return self.quote_balance

    def available_base_balance(self, symbol):
        return self.position

    def normalize_quantity(self, symbol, quantity):
        return round(quantity, 3)

    def minimum_cost(self, symbol):
        return self._minimum_cost

    def place_market_order(self, symbol, side, quantity, client_order_id):
        self.orders.append((symbol, side.value, quantity, client_order_id))
        if self.fail_submit:
            raise TimeoutError("exchange timeout")
        return {"id": "exchange-1", "status": "closed", "clientOrderId": client_order_id}

    def reconciliation_orders(self, symbol=None):
        self.reconciliation_symbols.append(symbol)
        return [
            item for item in self.remote_open
            if symbol is None or item.get("symbol") in {None, symbol}
        ]

    def cancel_order(self, order_id, symbol=None):
        for index, item in enumerate(self.remote_open):
            if item.get("id") == order_id:
                return self.remote_open.pop(index)
        raise KeyError(order_id)


def config(**overrides):
    values = dict(
        enabled=True,
        exchange_id="kraken",
        market_type="spot",
        allowed_symbols=("BTC/USD",),
        max_order_notional=100.0,
        max_position_notional=500.0,
        max_daily_notional=100.0,
        max_quote_age_ms=15_000,
        arm_ttl_seconds=300,
        one_shot_arm=True,
    )
    values.update(overrides)
    return LiveTradingConfig(**values)


def service(tmp_path, **overrides):
    state = LiveStateStore(tmp_path / "live.sqlite3")
    return LiveExecutionService(config(**overrides), state, MemoryAuditLog()), state


def test_live_state_defaults_fail_closed(tmp_path):
    state = LiveStateStore(tmp_path / "live.sqlite3")

    status = state.status()

    assert status["armed"] is False
    assert status["kill_switch"] is True
    assert state.health()["ok"] is True


def test_live_order_requires_explicit_arm(tmp_path):
    live, _ = service(tmp_path)

    with pytest.raises(RiskRejected, match="disarmed"):
        live.submit_market(FakeLiveBroker(), "request_0001", "BTC/USD", Side.BUY, 1)


def test_live_order_is_one_shot_idempotent_and_uses_short_client_id(tmp_path):
    live, state = service(tmp_path)
    broker = FakeLiveBroker()
    state.arm(60)

    first = live.submit_market(broker, "request_0002", "BTC/USD", Side.BUY, 1)
    retry = live.submit_market(broker, "request_0002", "BTC/USD", Side.BUY, 1)

    assert first.status == "closed"
    assert retry == first
    assert len(broker.orders) == 1
    assert len(first.client_order_id) == 18
    assert state.status()["armed"] is False
    assert state.status()["kill_switch"] is True


def test_request_id_cannot_be_reused_for_different_order(tmp_path):
    live, state = service(tmp_path)
    broker = FakeLiveBroker()
    state.arm(60)
    live.submit_market(broker, "request_0003", "BTC/USD", Side.BUY, 1)

    with pytest.raises(RiskRejected, match="different live order"):
        live.submit_market(broker, "request_0003", "BTC/USD", Side.BUY, 1.1)


def test_daily_limit_is_atomic_with_arm_consumption(tmp_path):
    live, state = service(tmp_path)
    broker = FakeLiveBroker()
    state.arm(60)
    live.submit_market(broker, "request_0004", "BTC/USD", Side.BUY, 1)
    state.arm(60)

    with pytest.raises(RiskRejected, match="daily live notional"):
        live.submit_market(broker, "request_0005", "BTC/USD", Side.BUY, 1.1)

    assert len(broker.orders) == 1
    assert state.status()["armed"] is True


def test_unknown_exchange_outcome_is_not_retried(tmp_path):
    live, state = service(tmp_path)
    broker = FakeLiveBroker(fail_submit=True)
    state.arm(60)

    with pytest.raises(RuntimeError, match="outcome is unknown"):
        live.submit_market(broker, "request_0006", "BTC/USD", Side.BUY, 1)

    record = state.require("request_0006")
    assert record.status == "unknown"
    assert state.status()["armed"] is False
    assert len(broker.orders) == 1

    retry = live.submit_market(broker, "request_0006", "BTC/USD", Side.BUY, 1)
    assert retry.status == "unknown"
    assert len(broker.orders) == 1


def test_live_risk_rejects_stale_quote_insufficient_balance_and_short(tmp_path):
    stale, stale_state = service(tmp_path / "stale")
    stale_state.arm(60)
    old = int(time.time() * 1000) - 60_000
    with pytest.raises(RiskRejected, match="stale"):
        stale.submit_market(
            FakeLiveBroker(timestamp=old), "request_0007", "BTC/USD", Side.BUY, 1
        )

    cash, cash_state = service(tmp_path / "cash")
    cash_state.arm(60)
    with pytest.raises(RiskRejected, match="insufficient"):
        cash.submit_market(
            FakeLiveBroker(quote_balance=10), "request_0008", "BTC/USD", Side.BUY, 1
        )

    sell, sell_state = service(tmp_path / "sell")
    sell_state.arm(60)
    with pytest.raises(RiskRejected, match="short positions"):
        sell.submit_market(
            FakeLiveBroker(position=0), "request_0009", "BTC/USD", Side.SELL, 1
        )


def test_live_read_routes_require_auth_when_auth_enabled():
    app = FastAPI()
    install_security_middleware(
        app,
        SecurityConfig(
            require_auth=True,
            api_key="x" * 32,
            max_body_bytes=1024,
            mutation_rate_per_minute=10,
        ),
    )

    @app.get("/live/status")
    def live_status():
        return {"ok": True}

    @app.get("/public")
    def public():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/public").status_code == 200
    assert client.get("/live/status").status_code == 401
    assert (
        client.get("/live/status", headers={"Authorization": f"Bearer {'x' * 32}"}).status_code
        == 200
    )


def test_api_restart_and_backup_restore_fail_closed(tmp_path):
    from trading_platform.backup import create_backup, restore_backup

    data = tmp_path / "data"
    state = LiveStateStore(data / "live.sqlite3")
    state.arm(60)
    assert state.status()["armed"] is True

    restarted = LiveStateStore(data / "live.sqlite3", disarm_on_start=True)
    assert restarted.status()["armed"] is False

    restarted.arm(60)
    archive = create_backup(data, tmp_path / "backup.tar.gz")
    restore_backup(archive, tmp_path / "restored")
    restored = LiveStateStore(tmp_path / "restored" / "live.sqlite3")
    assert restored.status()["armed"] is False
    assert restored.status()["kill_switch"] is True


def test_reconciliation_resolves_unknown_by_client_order_id(tmp_path):
    live, state = service(tmp_path)
    broker = FakeLiveBroker(fail_submit=True)
    state.arm(60)
    with pytest.raises(RuntimeError):
        live.submit_market(broker, "request_0010", "BTC/USD", Side.BUY, 1)

    record = state.require("request_0010")
    broker.remote_open = [
        {
            "id": "exchange-recovered",
            "clientOrderId": record.client_order_id,
            "status": "closed",
        }
    ]
    result = live.reconcile_orders(broker)

    assert result["updated"] == 1
    assert result["unresolved_unknown"] == 0
    assert state.require("request_0010").status == "closed"


def test_emergency_stop_disarms_and_cancels_only_platform_owned_orders(tmp_path):
    live, state = service(tmp_path)
    broker = FakeLiveBroker()
    state.arm(60)
    owned = live.submit_market(broker, "request_0012", "BTC/USD", Side.BUY, 1)
    state.arm(60)
    broker.remote_open = [
        {
            "id": "open-owned",
            "clientOrderId": owned.client_order_id,
            "symbol": "BTC/USD",
            "status": "open",
        },
        {
            "id": "open-unrelated",
            "clientOrderId": "someone-else",
            "symbol": "BTC/USD",
            "status": "open",
        },
    ]

    result = live.emergency_stop(broker)

    assert result["kill_switch"] is True
    assert result["armed"] is False
    assert result["cancelled"] == 1
    assert [item["id"] for item in broker.remote_open] == ["open-unrelated"]
    assert state.status()["armed"] is False


def test_restart_marks_inflight_live_order_unknown(tmp_path):
    state = LiveStateStore(tmp_path / "live.sqlite3")
    state.arm(60)
    record, created = state.reserve(
        "request_0011",
        "kraken",
        "BTC/USD",
        Side.BUY,
        1,
        50,
        requested_quantity=1,
        max_daily_notional=100,
    )
    assert created is True
    assert record.status == "reserved"

    restarted = LiveStateStore(tmp_path / "live.sqlite3", disarm_on_start=True)

    recovered = restarted.require("request_0011")
    assert recovered.status == "unknown"
    assert "reconciliation required" in recovered.error
    assert restarted.status()["armed"] is False


def test_live_config_refuses_non_one_shot_when_enabled(monkeypatch):
    monkeypatch.setenv("TRADING_LIVE_ENABLED", "1")
    monkeypatch.setenv("TRADING_LIVE_ONE_SHOT_ARM", "0")

    with pytest.raises(RuntimeError, match="one-shot"):
        LiveTradingConfig.from_env()


def test_live_rejects_hedged_position_mode_and_exchange_minimum_cost(tmp_path):
    live, state = service(tmp_path / "hedged")
    state.arm(60)
    with pytest.raises(RiskRejected, match="one-way"):
        live.submit_market(
            FakeLiveBroker(hedged=True), "request_0013", "BTC/USD", Side.BUY, 1
        )

    live2, state2 = service(tmp_path / "mincost")
    state2.arm(60)
    with pytest.raises(RiskRejected, match="minimum cost"):
        live2.submit_market(
            FakeLiveBroker(price=50, minimum_cost=60),
            "request_0014",
            "BTC/USD",
            Side.BUY,
            1,
        )


def test_live_config_accepts_binance_future_market(monkeypatch):
    monkeypatch.setenv("TRADING_LIVE_EXCHANGE", "binance")
    monkeypatch.setenv("TRADING_LIVE_MARKET_TYPE", "future")
    monkeypatch.setenv("TRADING_LIVE_ALLOWED_SYMBOLS", "BTC/USDT:USDT")

    cfg = LiveTradingConfig.from_env()

    assert cfg.exchange_id == "binance"
    assert cfg.market_type == "future"
    assert cfg.allowed_symbols == ("BTC/USDT:USDT",)


def test_ccxt_live_broker_handles_contract_position_and_reduce_only_sell():
    from trading_platform.live import CCXTLiveBroker

    class Exchange:
        def __init__(self):
            self.created = None
            self.loaded = False

        def load_markets(self):
            self.loaded = True
            return {}

        def market(self, symbol):
            assert self.loaded is True
            return {
                "base": "BTC",
                "quote": "USDT",
                "contract": True,
                "contractSize": 1.0,
                "limits": {"amount": {"min": 0.001, "max": 1000}, "cost": {"min": 50}},
            }

        def fetch_position_mode(self):
            return {"hedged": False}

        def sapiGetAccountApiRestrictions(self):
            return {
                "enableReading": True,
                "enableFutures": True,
                "enableWithdrawals": False,
                "ipRestrict": True,
                "enableSpotAndMarginTrading": False,
            }

        def fetch_positions(self, symbols):
            return [
                {
                    "symbol": symbols[0],
                    "side": "long",
                    "contracts": 0.002,
                    "contractSize": 1.0,
                }
            ]

        def fetch_balance(self):
            return {"free": {"USDT": 500.0}, "total": {"USDT": 500.0}}

        def amount_to_precision(self, symbol, quantity):
            return f"{quantity:.3f}"

        def create_order(self, symbol, order_type, side, quantity, price, params):
            self.created = (symbol, order_type, side, quantity, price, params)
            return {"id": "future-1", "status": "closed", "clientOrderId": params["clientOrderId"]}

    exchange = Exchange()
    broker = CCXTLiveBroker("binance", "future", exchange=exchange)

    assert broker.position_mode_hedged() is False
    assert broker.private_security_posture() == {
        "reading": True,
        "futures": True,
        "withdrawals": False,
        "ip_restricted": True,
        "spot_margin": False,
    }
    assert broker.position_quantity("BTC/USDT:USDT") == pytest.approx(0.002)
    assert broker.available_base_balance("BTC/USDT:USDT") == pytest.approx(0.002)
    assert broker.minimum_cost("BTC/USDT:USDT") == 50
    broker.place_market_order("BTC/USDT:USDT", Side.SELL, 0.001, "client123")

    assert exchange.created[-1]["clientOrderId"] == "client123"
    assert exchange.created[-1]["reduceOnly"] is True


def test_reconciliation_queries_each_allowlisted_symbol(tmp_path):
    live, _ = service(
        tmp_path,
        allowed_symbols=("BTC/USD", "ETH/USD"),
    )
    broker = FakeLiveBroker()
    broker.remote_open = [
        {"id": "btc-1", "clientOrderId": "other-btc", "status": "closed", "symbol": "BTC/USD"},
        {"id": "eth-1", "clientOrderId": "other-eth", "status": "closed", "symbol": "ETH/USD"},
    ]

    result = live.reconcile_orders(broker)

    assert broker.reconciliation_symbols == ["BTC/USD", "ETH/USD"]
    assert result["remote_orders"] == 2
