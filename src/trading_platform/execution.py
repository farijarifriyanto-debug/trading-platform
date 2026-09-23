from .domain import Fill, OrderIntent, Side
from .portfolio import Portfolio
from .risk import RiskManager


class PaperBroker:
    def __init__(self, portfolio: Portfolio | None = None, risk: RiskManager | None = None,
                 fee_bps: float = 5.0, slippage_bps: float = 2.0):
        self.portfolio = portfolio or Portfolio()
        self.risk = risk or RiskManager()
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def submit(self, order: OrderIntent) -> Fill:
        self.risk.validate(order, self.portfolio.position(order.symbol))
        direction = 1 if order.side == Side.BUY else -1
        price = order.price * (1 + direction * self.slippage_bps / 10_000)
        fee = order.quantity * price * self.fee_bps / 10_000
        fill = Fill(order.symbol, order.side, order.quantity, price, fee)
        self.portfolio.apply(fill)
        return fill
