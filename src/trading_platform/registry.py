from dataclasses import dataclass
from typing import Any, Callable

from .domain import Side
from .strategy import sma_signal

SignalFunction = Callable[[list[float], dict[str, Any]], Side | None]


@dataclass(frozen=True)
class StrategyDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    signal: SignalFunction

    def public_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class StrategyRegistry:
    def __init__(self):
        self._strategies: dict[str, StrategyDefinition] = {}

    def register(self, definition: StrategyDefinition) -> None:
        if not definition.name or definition.name in self._strategies:
            raise ValueError(f"strategy already registered or invalid: {definition.name}")
        self._strategies[definition.name] = definition

    def get(self, name: str) -> StrategyDefinition:
        try:
            return self._strategies[name]
        except KeyError as exc:
            raise KeyError(f"unknown strategy: {name}") from exc

    def catalog(self) -> list[dict[str, Any]]:
        return [self._strategies[name].public_metadata() for name in sorted(self._strategies)]

    def evaluate(
        self,
        name: str,
        prices: list[float],
        parameters: dict[str, Any] | None = None,
    ) -> Side | None:
        definition = self.get(name)
        merged = dict(definition.parameters)
        if parameters:
            merged.update(parameters)
        return definition.signal(prices, merged)


def _sma(prices: list[float], parameters: dict[str, Any]) -> Side | None:
    return sma_signal(prices, fast=int(parameters["fast"]), slow=int(parameters["slow"]))


def default_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(
        StrategyDefinition(
            name="sma_trend",
            description="Fast/slow simple moving-average trend state used by the paper reference strategy.",
            parameters={"fast": 5, "slow": 20},
            signal=_sma,
        )
    )
    return registry
