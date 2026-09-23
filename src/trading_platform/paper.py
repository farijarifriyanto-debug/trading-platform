from dataclasses import asdict, dataclass
from typing import Any

from .audit import AuditSink
from .domain import Fill, OrderIntent, Side
from .execution import PaperBroker
from .market import MarketData
from .registry import StrategyRegistry, default_strategy_registry


@dataclass(frozen=True)
class StrategyStep:
    exchange: str
    symbol: str
    strategy: str
    signal: Side | None
    action: str
    fill: Fill | None
    candles_used: int
    parameters: dict[str, Any]


class PaperTradingService:
    """Orchestrates market data -> strategy -> risk -> paper execution -> audit."""

    def __init__(
        self,
        broker: PaperBroker,
        audit: AuditSink,
        strategies: StrategyRegistry | None = None,
    ):
        self.broker = broker
        self.audit = audit
        self.strategies = strategies or default_strategy_registry()

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

    def strategy_step(
        self,
        market: MarketData,
        symbol: str,
        strategy: str,
        parameters: dict[str, Any] | None = None,
        quantity: float = 1.0,
        timeframe: str = "1h",
        limit: int = 200,
    ) -> StrategyStep:
        candles = market.candles(symbol, timeframe=timeframe, limit=limit)
        prices = [candle.close for candle in candles]
        definition = self.strategies.get(strategy)
        merged_parameters = dict(definition.parameters)
        if parameters:
            merged_parameters.update(parameters)
        signal = self.strategies.evaluate(strategy, prices, merged_parameters)
        position = self.broker.portfolio.position(symbol)

        action = "hold"
        fill: Fill | None = None
        if signal == Side.BUY and position <= 0:
            fill = self.market_order(market, symbol, Side.BUY, quantity)
            action = "buy"
        elif signal == Side.SELL and position > 0:
            fill = self.market_order(market, symbol, Side.SELL, min(quantity, position))
            action = "sell"

        step = StrategyStep(
            market.exchange_id,
            symbol,
            strategy,
            signal,
            action,
            fill,
            len(candles),
            merged_parameters,
        )
        self.audit.record(
            "strategy_step",
            {
                "strategy": strategy,
                "exchange": market.exchange_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "parameters": merged_parameters,
                "signal": signal.value if signal else None,
                "action": action,
                "candles_used": len(candles),
            },
        )
        return step

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
        return self.strategy_step(
            market=market,
            symbol=symbol,
            strategy="sma_trend",
            parameters={"fast": fast, "slow": slow},
            quantity=quantity,
            timeframe=timeframe,
            limit=limit,
        )
