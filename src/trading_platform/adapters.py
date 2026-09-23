from typing import Any


class CCXTMarketData:
    """Optional crypto market-data adapter. Install with: pip install .[crypto]"""

    def __init__(self, exchange_id: str = "binance"):
        try:
            import ccxt
        except ImportError as exc:
            raise RuntimeError("ccxt extra is not installed") from exc
        exchange_cls = getattr(ccxt, exchange_id)
        self.exchange = exchange_cls({"enableRateLimit": True})

    def ticker(self, symbol: str) -> dict[str, Any]:
        return self.exchange.fetch_ticker(symbol)

    def ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[list[Any]]:
        return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
