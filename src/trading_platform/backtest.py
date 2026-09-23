from dataclasses import dataclass

from .domain import OrderIntent, Side
from .execution import PaperBroker
from .portfolio import Portfolio
from .strategy import sma_signal


@dataclass(frozen=True)
class BacktestResult:
    start_cash: float
    end_equity: float
    trades: int


def run_sma_backtest(prices: list[float], symbol: str = "TEST/USD",
                     quantity: float = 1.0, fast: int = 5, slow: int = 20) -> BacktestResult:
    portfolio = Portfolio()
    broker = PaperBroker(portfolio=portfolio)
    start_cash = portfolio.cash
    trades = 0
    for i, price in enumerate(prices, start=1):
        signal = sma_signal(prices[:i], fast=fast, slow=slow)
        if signal == Side.BUY and portfolio.position(symbol) <= 0:
            broker.submit(OrderIntent(symbol, Side.BUY, quantity, price))
            trades += 1
        elif signal == Side.SELL and portfolio.position(symbol) > 0:
            broker.submit(OrderIntent(symbol, Side.SELL, quantity, price))
            trades += 1
    mark = prices[-1] if prices else 0.0
    return BacktestResult(start_cash, portfolio.equity({symbol: mark}), trades)
