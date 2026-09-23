from dataclasses import dataclass
from typing import Any


class VectorBTUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VectorBTResult:
    fast: int
    slow: int
    initial_cash: float
    final_value: float
    total_return: float
    trades: int


class VectorBTResearch:
    """Optional VectorBT research adapter. It has no execution capability."""

    def __init__(self, vbt: Any | None = None):
        if vbt is not None:
            self.vbt = vbt
            return
        try:
            import vectorbt as vbt_module
        except ImportError as exc:
            raise VectorBTUnavailable(
                "VectorBT is not installed; install with `pip install -e '.[research]'`"
            ) from exc
        self.vbt = vbt_module

    def sma_cross(
        self,
        prices: list[float],
        fast: int = 10,
        slow: int = 50,
        initial_cash: float = 100_000.0,
        fees: float = 0.0005,
        quantity: float | None = None,
    ) -> VectorBTResult:
        if fast <= 0 or slow <= fast:
            raise ValueError("require 0 < fast < slow")
        if len(prices) < slow:
            raise ValueError("not enough prices for slow moving average")

        fast_ma = self.vbt.MA.run(prices, fast)
        slow_ma = self.vbt.MA.run(prices, slow)
        entries = fast_ma.ma_crossed_above(slow_ma)
        exits = fast_ma.ma_crossed_below(slow_ma)
        portfolio_kwargs = {
            "init_cash": initial_cash,
            "fees": fees,
        }
        if quantity is not None:
            portfolio_kwargs["size"] = quantity
        portfolio = self.vbt.Portfolio.from_signals(
            prices,
            entries,
            exits,
            **portfolio_kwargs,
        )
        values = portfolio.value()
        final_value = values.iloc[-1] if hasattr(values, "iloc") else values[-1]
        return VectorBTResult(
            fast=fast,
            slow=slow,
            initial_cash=initial_cash,
            final_value=float(final_value),
            total_return=float(portfolio.total_return()),
            trades=int(portfolio.trades.count()),
        )
