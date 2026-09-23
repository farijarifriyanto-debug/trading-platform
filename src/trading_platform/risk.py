from dataclasses import dataclass

from .domain import OrderIntent


@dataclass(frozen=True)
class RiskPolicy:
    max_order_notional: float = 10_000.0
    max_position_notional: float = 25_000.0


class RiskRejected(ValueError):
    pass


class RiskManager:
    def __init__(self, policy: RiskPolicy | None = None):
        self.policy = policy or RiskPolicy()

    def validate(self, order: OrderIntent, current_position: float = 0.0) -> None:
        if order.quantity <= 0 or order.price <= 0:
            raise RiskRejected("quantity and price must be positive")
        notional = order.quantity * order.price
        if notional > self.policy.max_order_notional:
            raise RiskRejected("order notional exceeds limit")
        signed = order.quantity if order.side.value == "buy" else -order.quantity
        projected = abs(current_position + signed) * order.price
        if projected > self.policy.max_position_notional:
            raise RiskRejected("projected position exceeds limit")
