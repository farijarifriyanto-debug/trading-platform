from dataclasses import dataclass

from .backtest import run_sma_backtest


@dataclass(frozen=True)
class SweepResult:
    fast: int
    slow: int
    start_cash: float
    end_equity: float
    return_fraction: float
    trades: int


@dataclass(frozen=True)
class SweepReport:
    evaluated: int
    skipped: int
    results: tuple[SweepResult, ...]


def run_sma_parameter_sweep(
    prices: list[float],
    fast_values: list[int],
    slow_values: list[int],
    symbol: str = "TEST/USD",
    quantity: float = 1.0,
) -> SweepReport:
    if not prices:
        raise ValueError("prices must not be empty")
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    results: list[SweepResult] = []
    skipped = 0
    seen: set[tuple[int, int]] = set()

    for fast in fast_values:
        for slow in slow_values:
            pair = (int(fast), int(slow))
            if pair in seen:
                continue
            seen.add(pair)
            fast_i, slow_i = pair
            if fast_i <= 0 or slow_i <= fast_i or slow_i > len(prices):
                skipped += 1
                continue

            result = run_sma_backtest(
                prices,
                symbol=symbol,
                quantity=quantity,
                fast=fast_i,
                slow=slow_i,
            )
            return_fraction = (result.end_equity - result.start_cash) / result.start_cash
            results.append(
                SweepResult(
                    fast=fast_i,
                    slow=slow_i,
                    start_cash=result.start_cash,
                    end_equity=result.end_equity,
                    return_fraction=return_fraction,
                    trades=result.trades,
                )
            )

    results.sort(key=lambda item: (-item.end_equity, item.fast, item.slow))
    return SweepReport(evaluated=len(results), skipped=skipped, results=tuple(results))
