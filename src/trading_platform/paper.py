from dataclasses import asdict, dataclass

from .audit import AuditSink
from .domain import Fill, OrderIntent, Side
from .execution import PaperBroker
from .market import MarketData
from .strategy import sma_signal


@dataclass(frozen=True)
class StrategyStep:
    exchange: str
    symbol: str
    signal: Side | None
    action: str
    fill: Fill | None
    candles_used: int


class PaperTradingService:
    """Orchestrates market data -> strategy -> risk -> paper execution -> audit."""

    def __init__(self, broker: PaperBroker, audit: AuditSink):
        self.broker = broker
        self.audit = audit

    def market_order(self, market: MarketData, symbol: str, side: Side, quantity: float) -> Fill:
        quote = market.quote(symbol)
        price = quote.executable_price(side)
        fill = self.broker.submit(OrderIntent(symbol, side, quantity, price))
        self.audit.record(
            "paper_fill",
            {
                "exchange": market.exchange_id,
                "source": "market_order",
                "quote": asdict(quote),
                "fill": asdict(fill),
            },
        )
        return fill

    def sma_step(
        self,
        market: MarketData,
        symbol: str,
        quantity: float = 1.0,
        timeframe: str = "1h",
        limit: int = 200,
        fast: int = 5,
        slow: int = 20,
    ) -> StrategyStep:
        candles = market.candles(symbol, timeframe=timeframe, limit=limit)
        prices = [candle.close for candle in candles]
        signal = sma_signal(prices, fast=fast, slow=slow)
        position = self.broker.portfolio.position(symbol)

        action = "hold"
        fill: Fill | None = None
        if signal == Side.BUY and position <= 0:
            fill = self.market_order(market, symbol, Side.BUY, quantity)
            action = "buy"
        elif signal == Side.SELL and position > 0:
            fill = self.market_order(market, symbol, Side.SELL, min(quantity, position))
            action = "sell"

        step = StrategyStep(market.exchange_id, symbol, signal, action, fill, len(candles))
        self.audit.record(
            "strategy_step",
            {
                "strategy": "sma_cross",
                "exchange": market.exchange_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "fast": fast,
                "slow": slow,
                "signal": signal.value if signal else None,
                "action": action,
                "candles_used": len(candles),
            },
        )
        return step
