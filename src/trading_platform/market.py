from dataclasses import dataclass
from typing import Any, Protocol

from .domain import Side


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


@dataclass(frozen=True)
class Quote:
    exchange: str
    symbol: str
    bid: float | None
    ask: float | None
    last: float | None
    timestamp: int | None = None

    def executable_price(self, side: Side) -> float:
        candidates = (self.ask, self.last, self.bid) if side == Side.BUY else (self.bid, self.last, self.ask)
        for candidate in candidates:
            if candidate is not None and candidate > 0:
                return candidate
        raise ValueError(f"no executable price for {self.exchange}:{self.symbol}")


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketData(Protocol):
    exchange_id: str

    def quote(self, symbol: str) -> Quote: ...

    def candles(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[Candle]: ...


class CCXTMarketData:
    """Read-only market data through CCXT public endpoints."""

    def __init__(self, exchange_id: str = "kraken", exchange: Any | None = None):
        self.exchange_id = exchange_id
        if exchange is not None:
            self.exchange = exchange
            return
        try:
            import ccxt
        except ImportError as exc:
            raise RuntimeError("CCXT is not installed; install with `pip install -e '.[crypto]'`") from exc
        exchange_cls = getattr(ccxt, exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"unknown CCXT exchange: {exchange_id}")
        self.exchange = exchange_cls({"enableRateLimit": True})

    def quote(self, symbol: str) -> Quote:
        raw = self.exchange.fetch_ticker(symbol)
        return Quote(
            exchange=self.exchange_id,
            symbol=symbol,
            bid=_number(raw.get("bid")),
            ask=_number(raw.get("ask")),
            last=_number(raw.get("last") or raw.get("close")),
            timestamp=raw.get("timestamp"),
        )

    def candles(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> list[Candle]:
        rows = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        candles: list[Candle] = []
        for row in rows:
            if len(row) < 6:
                raise ValueError("CCXT OHLCV row must contain timestamp, OHLC and volume")
            candles.append(
                Candle(
                    timestamp=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return candles
