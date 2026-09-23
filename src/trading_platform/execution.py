from .domain import Fill, OrderIntent, Side
from .portfolio import Portfolio
from .risk import RiskManager, RiskRejected


class PaperBroker:
    def __init__(
        self,
        portfolio: Portfolio | None = None,
        risk: RiskManager | None = None,
        fee_bps: float = 5.0,
        slippage_bps: float = 2.0,
    ):
        self.portfolio = portfolio or Portfolio()
        self.risk = risk or RiskManager()
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def submit(self, order: OrderIntent) -> Fill:
        direction = 1 if order.side == Side.BUY else -1
        price = order.price * (1 + direction * self.slippage_bps / 10_000)
        adjusted_order = OrderIntent(order.symbol, order.side, order.quantity, price)
        self.risk.validate(adjusted_order, self.portfolio.position(order.symbol))

        fee = order.quantity * price * self.fee_bps / 10_000
        if order.side == Side.BUY and order.quantity * price + fee > self.portfolio.cash:
            raise RiskRejected("insufficient paper cash")

        fill = Fill(order.symbol, order.side, order.quantity, price, fee)
        self.portfolio.apply(fill)
        return fill
