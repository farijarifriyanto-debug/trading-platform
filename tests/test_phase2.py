from pathlib import Path

import pytest

from trading_platform.audit import JSONLAuditLog, MemoryAuditLog
from trading_platform.domain import OrderIntent, Side
from trading_platform.execution import PaperBroker
from trading_platform.market import CCXTMarketData, Candle, Quote
from trading_platform.paper import PaperTradingService
from trading_platform.portfolio import Portfolio
from trading_platform.research import VectorBTResearch
from trading_platform.risk import RiskRejected


class FakeMarket:
    exchange_id = "fake"

    def __init__(self, closes):
        self._closes = closes

    def quote(self, symbol):
        return Quote("fake", symbol, bid=99.0, ask=101.0, last=100.0, timestamp=1)

    def candles(self, symbol, timeframe="1h", limit=200):
        return [
            Candle(i, price, price, price, price, 1.0)
            for i, price in enumerate(self._closes[-limit:], start=1)
        ]


def test_market_order_uses_side_aware_quote_and_audits():
    broker = PaperBroker(fee_bps=0, slippage_bps=0)
    audit = MemoryAuditLog()
    service = PaperTradingService(broker, audit)

    fill = service.market_order(FakeMarket([1, 2]), "BTC/USD", Side.BUY, 1)

    assert fill.price == 101.0
    assert broker.portfolio.position("BTC/USD") == 1
    assert audit.events[-1]["event_type"] == "paper_fill"


def test_default_risk_policy_rejects_short():
    broker = PaperBroker(fee_bps=0, slippage_bps=0)
    with pytest.raises(RiskRejected, match="short positions"):
        broker.submit(OrderIntent("BTC/USD", Side.SELL, 1, 100))


def test_sma_step_executes_buy_end_to_end():
    market = FakeMarket([1, 1, 1, 2, 3])
    broker = PaperBroker(fee_bps=0, slippage_bps=0)
    audit = MemoryAuditLog()
    service = PaperTradingService(broker, audit)

    result = service.sma_step(market, "BTC/USD", quantity=1, limit=5, fast=2, slow=5)

    assert result.signal == Side.BUY
    assert result.action == "buy"
    assert result.fill is not None
    assert broker.portfolio.position("BTC/USD") == 1
    assert [event["event_type"] for event in audit.events] == ["paper_fill", "strategy_step"]


def test_jsonl_audit_is_persistent(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    first = JSONLAuditLog(path)
    first.record("one", {"value": 1})
    second = JSONLAuditLog(path)
    second.record("two", {"value": 2})

    assert [event["event_type"] for event in second.tail(10)] == ["one", "two"]


class _FakeSeries(list):
    @property
    def iloc(self):
        return self


class _FakeMAResult:
    def ma_crossed_above(self, _):
        return [False, False, True]

    def ma_crossed_below(self, _):
        return [False, False, False]


class _FakeMA:
    @staticmethod
    def run(prices, window):
        return _FakeMAResult()


class _FakeTrades:
    def count(self):
        return 1


class _FakePortfolioResult:
    trades = _FakeTrades()

    def value(self):
        return _FakeSeries([100.0, 101.0, 105.0])

    def total_return(self):
        return 0.05


class _FakePortfolio:
    @staticmethod
    def from_signals(prices, entries, exits, init_cash, fees):
        return _FakePortfolioResult()


class _FakeVBT:
    MA = _FakeMA
    Portfolio = _FakePortfolio


def test_vectorbt_adapter_is_research_only_and_reports_metrics():
    result = VectorBTResearch(_FakeVBT).sma_cross([1, 2, 3], fast=1, slow=2, initial_cash=100)

    assert result.final_value == 105.0
    assert result.total_return == 0.05
    assert result.trades == 1


def test_paper_broker_rejects_buy_beyond_cash():
    broker = PaperBroker(portfolio=Portfolio(cash=50), fee_bps=0, slippage_bps=0)
    with pytest.raises(RiskRejected, match="insufficient paper cash"):
        broker.submit(OrderIntent("BTC/USD", Side.BUY, 1, 100))


class _FakeCCXTExchange:
    def fetch_ticker(self, symbol):
        return {"bid": 99, "ask": 101, "last": 100, "timestamp": 123}

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=200):
        return [[1, 90, 110, 80, 100, 12.5]][:limit]


def test_ccxt_adapter_normalizes_public_market_data_without_network():
    market = CCXTMarketData("fake", exchange=_FakeCCXTExchange())

    quote = market.quote("BTC/USD")
    candles = market.candles("BTC/USD", limit=1)

    assert quote.bid == 99.0
    assert quote.ask == 101.0
    assert quote.executable_price(Side.BUY) == 101.0
    assert candles[0].close == 100.0
    assert candles[0].volume == 12.5
