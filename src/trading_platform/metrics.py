from dataclasses import dataclass
from typing import Iterable

from .audit import JSONLAuditLog
from .datasets import HistoricalDatasetStore
from .portfolio import Portfolio


@dataclass(frozen=True)
class PlatformMetrics:
    dataset_count: int
    paper_cash: float
    open_positions: int
    net_position_units: float
    audit_events: int


def collect_metrics(
    datasets: HistoricalDatasetStore,
    portfolio: Portfolio,
    audit: JSONLAuditLog,
) -> PlatformMetrics:
    positions = [quantity for quantity in portfolio.positions.values() if quantity != 0]
    return PlatformMetrics(
        dataset_count=len(datasets.list_metadata()),
        paper_cash=portfolio.cash,
        open_positions=len(positions),
        net_position_units=sum(positions),
        audit_events=len(audit.tail(1000)),
    )
