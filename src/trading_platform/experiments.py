import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

ExperimentStatus = Literal["planned", "running", "completed", "failed"]


@dataclass(frozen=True)
class ExperimentSpec:
    dataset_id: str
    engine: str
    strategy: str
    parameters: dict[str, Any]
    initial_cash: float = 100_000.0
    quantity: float | None = None


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    spec: ExperimentSpec
    status: ExperimentStatus
    result: dict[str, Any] | None = None
    error: str | None = None


def experiment_id(spec: ExperimentSpec) -> str:
    payload = asdict(spec)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ExperimentStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def create(self, spec: ExperimentSpec) -> ExperimentRecord:
        record = ExperimentRecord(experiment_id(spec), spec, "planned")
        existing = self.get(record.experiment_id)
        if existing is not None:
            return existing
        self._write(record)
        return record

    def update(
        self,
        experiment_id_value: str,
        status: ExperimentStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ExperimentRecord:
        current = self.require(experiment_id_value)
        record = ExperimentRecord(
            experiment_id=current.experiment_id,
            spec=current.spec,
            status=status,
            result=result,
            error=error,
        )
        self._write(record)
        return record

    def get(self, experiment_id_value: str) -> ExperimentRecord | None:
        if not self._valid_id(experiment_id_value):
            raise ValueError("experiment_id must be a lowercase SHA-256")
        path = self.root / f"{experiment_id_value}.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        spec = ExperimentSpec(**raw["spec"])
        if experiment_id(spec) != experiment_id_value:
            raise ValueError("experiment integrity check failed")
        return ExperimentRecord(
            experiment_id=experiment_id_value,
            spec=spec,
            status=raw["status"],
            result=raw.get("result"),
            error=raw.get("error"),
        )

    def require(self, experiment_id_value: str) -> ExperimentRecord:
        record = self.get(experiment_id_value)
        if record is None:
            raise FileNotFoundError(experiment_id_value)
        return record

    def records(self) -> list[ExperimentRecord]:
        if not self.root.exists():
            return []
        records = []
        for path in sorted(self.root.glob("*.json")):
            try:
                record = self.get(path.stem)
            except (ValueError, KeyError, TypeError):
                continue
            if record is not None:
                records.append(record)
        return records

    def compare(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        rows = []
        for record in self.records():
            if record.status != "completed" or record.result is None:
                continue
            if dataset_id is not None and record.spec.dataset_id != dataset_id:
                continue
            rows.append(
                {
                    "experiment_id": record.experiment_id,
                    "dataset_id": record.spec.dataset_id,
                    "engine": record.spec.engine,
                    "strategy": record.spec.strategy,
                    "parameters": record.spec.parameters,
                    "start_cash": record.result.get("start_cash"),
                    "end_equity": record.result.get("end_equity"),
                    "return_fraction": record.result.get("return_fraction"),
                    "trades": record.result.get("trades", record.result.get("fills")),
                }
            )
        rows.sort(key=lambda row: (row["dataset_id"], row["strategy"], row["engine"], row["experiment_id"]))
        return rows

    def _write(self, record: ExperimentRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{record.experiment_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(asdict(record), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)

    @staticmethod
    def _valid_id(value: str) -> bool:
        return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
