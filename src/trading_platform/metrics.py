from dataclasses import dataclass

from .audit import JSONLAuditLog
from .datasets import HistoricalDatasetStore
from .experiments import ExperimentStore
from .portfolio import Portfolio


@dataclass(frozen=True)
class PlatformMetrics:
    dataset_count: int
    paper_cash: float
    open_positions: int
    net_position_units: float
    audit_events: int
    experiment_count: int
    completed_experiments: int


def collect_metrics(
    datasets: HistoricalDatasetStore,
    portfolio: Portfolio,
    audit: JSONLAuditLog,
    experiments: ExperimentStore | None = None,
) -> PlatformMetrics:
    positions = [quantity for quantity in portfolio.positions.values() if quantity != 0]
    experiment_records = experiments.records() if experiments is not None else []
    return PlatformMetrics(
        dataset_count=len(datasets.list_metadata()),
        paper_cash=portfolio.cash,
        open_positions=len(positions),
        net_position_units=sum(positions),
        audit_events=len(audit.tail(1000)),
        experiment_count=len(experiment_records),
        completed_experiments=sum(
            1 for record in experiment_records if record.status == "completed"
        ),
    )
