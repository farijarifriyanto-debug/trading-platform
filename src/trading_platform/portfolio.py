from dataclasses import dataclass, field

from .domain import Fill, Side


@dataclass
class Portfolio:
    cash: float = 100_000.0
    positions: dict[str, float] = field(default_factory=dict)

    def apply(self, fill: Fill) -> None:
        signed = fill.quantity if fill.side == Side.BUY else -fill.quantity
        self.positions[fill.symbol] = self.positions.get(fill.symbol, 0.0) + signed
        gross = fill.quantity * fill.price
        self.cash += (-gross if fill.side == Side.BUY else gross) - fill.fee

    def position(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + sum(qty * marks.get(symbol, 0.0) for symbol, qty in self.positions.items())
