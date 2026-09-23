import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .datasets import HistoricalDataset


@dataclass(frozen=True)
class SimulationSpec:
    engine: str
    dataset_id: str
    symbol: str
    strategy: str
    parameters: dict[str, Any]
    initial_cash: float = 100_000.0
    base_currency: str = "USD"


@dataclass(frozen=True)
class SimulationPlan:
    plan_id: str
    engine: str
    dataset_id: str
    live_mode: bool
    manifest: dict[str, Any]


class SimulationAdapter(Protocol):
    engine: str

    def plan(self, dataset: HistoricalDataset, spec: SimulationSpec) -> SimulationPlan: ...


def _plan_id(manifest: dict[str, Any]) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class NautilusSimulationAdapter:
    """Builds a safe NautilusTrader backtest plan; it never starts an engine."""

    engine = "nautilus"

    def plan(self, dataset: HistoricalDataset, spec: SimulationSpec) -> SimulationPlan:
        manifest = {
            "schema_version": 1,
            "engine": self.engine,
            "mode": "backtest",
            "dataset": dataset.metadata(),
            "strategy": {
                "name": spec.strategy,
                "parameters": spec.parameters,
            },
            "venue": {
                "name": "SIM",
                "account_type": "MARGIN",
                "oms_type": "HEDGING",
                "book_type": "L1_MBP",
                "starting_balances": [f"{spec.initial_cash:g} {spec.base_currency}"],
                "fee_model": "explicit_zero_baseline",
            },
            "data": {
                "source": "content_addressed_dataset",
                "dataset_id": dataset.dataset_id,
                "symbol": dataset.symbol,
                "timeframe": dataset.timeframe,
                "count": dataset.count,
            },
            "engine_config": {
                "shutdown_on_error": True,
            },
            "execution_enabled": False,
        }
        return SimulationPlan(
            plan_id=_plan_id(manifest),
            engine=self.engine,
            dataset_id=dataset.dataset_id,
            live_mode=False,
            manifest=manifest,
        )


class LeanSimulationAdapter:
    """Builds a LEAN backtest config boundary; live-mode is hard-disabled."""

    engine = "lean"

    def plan(self, dataset: HistoricalDataset, spec: SimulationSpec) -> SimulationPlan:
        manifest = {
            "schema_version": 1,
            "engine": self.engine,
            "mode": "backtest",
            "dataset": dataset.metadata(),
            "algorithm": {
                "strategy": spec.strategy,
                "parameters": spec.parameters,
                "symbol": spec.symbol,
            },
            "lean_config": {
                "live-mode": False,
                "algorithm-type-name": "TradingPlatformBoundaryAlgorithm",
                "cash": spec.initial_cash,
                "account-currency": spec.base_currency,
            },
            "data": {
                "source": "content_addressed_dataset",
                "dataset_id": dataset.dataset_id,
                "timeframe": dataset.timeframe,
                "count": dataset.count,
            },
            "execution_enabled": False,
        }
        return SimulationPlan(
            plan_id=_plan_id(manifest),
            engine=self.engine,
            dataset_id=dataset.dataset_id,
            live_mode=False,
            manifest=manifest,
        )


class SimulationRegistry:
    def __init__(self):
        self._adapters: dict[str, SimulationAdapter] = {}

    def register(self, adapter: SimulationAdapter) -> None:
        if adapter.engine in self._adapters:
            raise ValueError(f"simulation adapter already registered: {adapter.engine}")
        self._adapters[adapter.engine] = adapter

    def get(self, engine: str) -> SimulationAdapter:
        try:
            return self._adapters[engine]
        except KeyError as exc:
            raise KeyError(f"unknown simulation engine: {engine}") from exc

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "engine": name,
                "mode": "backtest",
                "execution_enabled": False,
            }
            for name in sorted(self._adapters)
        ]


def default_simulation_registry() -> SimulationRegistry:
    registry = SimulationRegistry()
    registry.register(NautilusSimulationAdapter())
    registry.register(LeanSimulationAdapter())
    return registry


class SimulationPlanStore:
    """Persists immutable simulation plans for provenance."""

    def __init__(self, root):
        from pathlib import Path

        self.root = Path(root)

    def save(self, plan: SimulationPlan) -> SimulationPlan:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{plan.plan_id}.json"
        record = asdict(plan)
        raw = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_text(raw + "\n", encoding="utf-8")
            tmp.replace(path)
        return plan

    def load(self, plan_id: str) -> SimulationPlan:
        if len(plan_id) != 64 or any(c not in "0123456789abcdef" for c in plan_id):
            raise ValueError("plan_id must be a lowercase SHA-256")
        path = self.root / f"{plan_id}.json"
        if not path.exists():
            raise FileNotFoundError(plan_id)
        record = json.loads(path.read_text(encoding="utf-8"))
        plan = SimulationPlan(**record)
        if _plan_id(plan.manifest) != plan.plan_id:
            raise ValueError("simulation plan integrity check failed")
        return plan

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        items = []
        for path in sorted(self.root.glob("*.json")):
            try:
                plan = self.load(path.stem)
            except (ValueError, FileNotFoundError, TypeError):
                continue
            items.append(
                {
                    "plan_id": plan.plan_id,
                    "engine": plan.engine,
                    "dataset_id": plan.dataset_id,
                    "live_mode": plan.live_mode,
                }
            )
        return items
