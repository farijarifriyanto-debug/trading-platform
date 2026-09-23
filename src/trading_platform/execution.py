from .domain import Fill, OrderIntent, Side
from .portfolio import Portfolio
from .risk import RiskManager, RiskRejected
from .state import PortfolioState


class PaperBroker:
    def __init__(
        self,
        portfolio: Portfolio | None = None,
        risk: RiskManager | None = None,
        fee_bps: float = 5.0,
        slippage_bps: float = 2.0,
        state: PortfolioState | None = None,
    ):
        self.portfolio = portfolio or Portfolio()
        self.risk = risk or RiskManager()
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self.state = state

    def submit(self, order: OrderIntent) -> Fill:
        direction = 1 if order.side == Side.BUY else -1
        price = order.price * (1 + direction * self.slippage_bps / 10_000)
        adjusted_order = OrderIntent(order.symbol, order.side, order.quantity, price)
        self.risk.validate(adjusted_order, self.portfolio.position(order.symbol))

        fee = order.quantity * price * self.fee_bps / 10_000
        if order.side == Side.BUY and order.quantity * price + fee > self.portfolio.cash:
            raise RiskRejected("insufficient paper cash")

        fill = Fill(order.symbol, order.side, order.quantity, price, fee)
        before_cash = self.portfolio.cash
        before_positions = dict(self.portfolio.positions)
        self.portfolio.apply(fill)
        if self.state is not None:
            try:
                self.state.save_portfolio(self.portfolio)
            except Exception:
                self.portfolio.cash = before_cash
                self.portfolio.positions = before_positions
                raise
        return fill
