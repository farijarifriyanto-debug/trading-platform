import pytest

from trading_platform.backtest import run_sma_backtest
from trading_platform.domain import OrderIntent, Side
from trading_platform.execution import PaperBroker
from trading_platform.risk import RiskPolicy, RiskRejected, RiskManager
from trading_platform.strategy import sma_signal


def test_sma_signal_buy():
    assert sma_signal([1, 1, 1, 2, 3], fast=2, slow=5) == Side.BUY


def test_risk_rejects_large_order():
    risk = RiskManager(RiskPolicy(max_order_notional=100))
    with pytest.raises(RiskRejected):
        risk.validate(OrderIntent("BTC/USDT", Side.BUY, 1, 101))


def test_paper_broker_updates_position():
    broker = PaperBroker(fee_bps=0, slippage_bps=0)
    fill = broker.submit(OrderIntent("ETH/USDT", Side.BUY, 2, 100))
    assert fill.price == 100
    assert broker.portfolio.position("ETH/USDT") == 2
    assert broker.portfolio.cash == 99_800


def test_backtest_runs():
    result = run_sma_backtest([10, 10, 9, 8, 7, 8, 9, 10, 11], fast=2, slow=4)
    assert result.trades >= 1
    assert result.end_equity > 0
