from .domain import Side


def sma_signal(prices: list[float], fast: int = 5, slow: int = 20) -> Side | None:
    if fast <= 0 or slow <= fast or len(prices) < slow:
        return None
    fast_avg = sum(prices[-fast:]) / fast
    slow_avg = sum(prices[-slow:]) / slow
    if fast_avg > slow_avg:
        return Side.BUY
    if fast_avg < slow_avg:
        return Side.SELL
    return None
