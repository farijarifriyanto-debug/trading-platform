import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .datasets import HistoricalDataset

CandidateStatus = Literal["planned", "running", "completed", "failed"]
GateStatus = Literal["RESEARCH_PASS", "REVIEW_REQUIRED"]


@dataclass(frozen=True)
class RobustnessPolicy:
    min_samples: int = 100
    min_folds: int = 3
    min_mean_accuracy: float = 0.52
    min_accuracy_uplift: float = 0.0
    max_accuracy_std: float = 0.15


@dataclass(frozen=True)
class AICandidateSpec:
    dataset_id: str
    model_family: str = "random_forest_direction"
    feature_version: str = "direction-v1"
    seed: int = 42
    folds: int = 3
    initial_train_fraction: float = 0.5
    hyperparameters: dict[str, Any] | None = None
    robustness: RobustnessPolicy = RobustnessPolicy()


@dataclass(frozen=True)
class AICandidateRecord:
    candidate_id: str
    spec: AICandidateSpec
    status: CandidateStatus
    gate_status: GateStatus | None = None
    gate_reasons: tuple[str, ...] = ()
    result: dict[str, Any] | None = None
    error: str | None = None


def candidate_id(spec: AICandidateSpec) -> str:
    raw = json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def evaluate_robustness(
    result: dict[str, Any],
    policy: RobustnessPolicy,
) -> tuple[GateStatus, tuple[str, ...]]:
    reasons = []
    if int(result["sample_count"]) < policy.min_samples:
        reasons.append(f"sample_count<{policy.min_samples}")
    if int(result["fold_count"]) < policy.min_folds:
        reasons.append(f"fold_count<{policy.min_folds}")
    if float(result["mean_accuracy"]) < policy.min_mean_accuracy:
        reasons.append(f"mean_accuracy<{policy.min_mean_accuracy}")
    if float(result["mean_accuracy_uplift"]) < policy.min_accuracy_uplift:
        reasons.append(f"mean_accuracy_uplift<{policy.min_accuracy_uplift}")
    if float(result["accuracy_std"]) > policy.max_accuracy_std:
        reasons.append(f"accuracy_std>{policy.max_accuracy_std}")
    return ("REVIEW_REQUIRED", tuple(reasons)) if reasons else ("RESEARCH_PASS", ())


class AICandidateStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def create(self, spec: AICandidateSpec) -> AICandidateRecord:
        record = AICandidateRecord(candidate_id(spec), spec, "planned")
        existing = self.get(record.candidate_id)
        if existing is not None:
            return existing
        self._write(record)
        return record

    def update(
        self,
        candidate_id_value: str,
        status: CandidateStatus,
        gate_status: GateStatus | None = None,
        gate_reasons: tuple[str, ...] = (),
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> AICandidateRecord:
        current = self.require(candidate_id_value)
        record = AICandidateRecord(
            candidate_id=current.candidate_id,
            spec=current.spec,
            status=status,
            gate_status=gate_status,
            gate_reasons=gate_reasons,
            result=result,
            error=error,
        )
        self._write(record)
        return record

    def get(self, candidate_id_value: str) -> AICandidateRecord | None:
        if len(candidate_id_value) != 64 or any(c not in "0123456789abcdef" for c in candidate_id_value):
            raise ValueError("candidate_id must be a lowercase SHA-256")
        path = self.root / f"{candidate_id_value}.json"
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        robustness = RobustnessPolicy(**raw["spec"].pop("robustness"))
        spec = AICandidateSpec(**raw["spec"], robustness=robustness)
        if candidate_id(spec) != candidate_id_value:
            raise ValueError("AI candidate integrity check failed")
        return AICandidateRecord(
            candidate_id=candidate_id_value,
            spec=spec,
            status=raw["status"],
            gate_status=raw.get("gate_status"),
            gate_reasons=tuple(raw.get("gate_reasons") or ()),
            result=raw.get("result"),
            error=raw.get("error"),
        )

    def require(self, candidate_id_value: str) -> AICandidateRecord:
        record = self.get(candidate_id_value)
        if record is None:
            raise FileNotFoundError(candidate_id_value)
        return record

    def records(self) -> list[AICandidateRecord]:
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

    def _write(self, record: AICandidateRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{record.candidate_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(asdict(record), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)


class AIResearchWorkerService:
    def __init__(
        self,
        store: AICandidateStore,
        models_root: str | Path,
        python: str | None = None,
        script: str | Path | None = None,
        timeout_seconds: int = 180,
    ):
        self.store = store
        self.models_root = Path(models_root)
        self.python = python or os.getenv(
            "ML_WORKER_PYTHON",
            "/home/botadmin/.venvs/trading-ml/bin/python",
        )
        self.script = Path(
            script
            or os.getenv(
                "ML_WORKER_SCRIPT",
                str(Path(__file__).resolve().parents[2] / "workers" / "ml_worker.py"),
            )
        )
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict[str, Any]:
        available = Path(self.python).is_file() and self.script.is_file()
        return {
            "available": available,
            "isolated": True,
            "model_family": "random_forest_direction",
            "execution_enabled": False,
            "live_mode": False,
        }

    def run(self, record: AICandidateRecord, dataset: HistoricalDataset) -> AICandidateRecord:
        if record.spec.model_family != "random_forest_direction":
            raise ValueError(f"unsupported model family: {record.spec.model_family}")
        if not Path(self.python).is_file() or not self.script.is_file():
            raise RuntimeError("ML research worker environment is not installed")
        self.store.update(record.candidate_id, "running")
        model_path = self.models_root / f"{record.candidate_id}.joblib"
        job = {
            "dataset_id": dataset.dataset_id,
            "candles": [asdict(candle) for candle in dataset.candles],
            "seed": record.spec.seed,
            "folds": record.spec.folds,
            "initial_train_fraction": record.spec.initial_train_fraction,
            "hyperparameters": record.spec.hyperparameters or {},
            "model_artifact_path": str(model_path),
        }
        try:
            with tempfile.TemporaryDirectory(prefix="trading-ml-") as temp:
                job_path = Path(temp) / "job.json"
                result_path = Path(temp) / "result.json"
                job_path.write_text(json.dumps(job, separators=(",", ":")), encoding="utf-8")
                completed = subprocess.run(
                    [self.python, str(self.script), "--job", str(job_path), "--result", str(result_path)],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                if completed.returncode != 0:
                    tail = (completed.stderr or completed.stdout)[-2000:]
                    raise RuntimeError(f"ML worker failed ({completed.returncode}): {tail}")
                result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("execution_enabled") is not False or result.get("live_mode") is not False:
                raise RuntimeError("ML worker violated research-only safety gate")
            gate_status, reasons = evaluate_robustness(result, record.spec.robustness)
            return self.store.update(
                record.candidate_id,
                "completed",
                gate_status=gate_status,
                gate_reasons=reasons,
                result=result,
            )
        except Exception as exc:
            self.store.update(record.candidate_id, "failed", error=str(exc))
            raise
