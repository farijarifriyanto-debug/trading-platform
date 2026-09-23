import json
import os
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .ai_research import AICandidateRecord
from .datasets import HistoricalDataset


class FinRLXResearchService:
    """Runs FinRL-X backtests in an isolated environment. No broker execution is exposed."""

    def __init__(
        self,
        python: str | None = None,
        script: str | Path | None = None,
        source: str | Path | None = None,
        source_commit: str | None = None,
        timeout_seconds: int = 180,
    ):
        self.python = python or os.getenv(
            "FINRLX_WORKER_PYTHON",
            "/home/botadmin/.venvs/trading-finrlx/bin/python",
        )
        self.script = Path(
            script
            or os.getenv(
                "FINRLX_WORKER_SCRIPT",
                str(Path(__file__).resolve().parents[2] / "workers" / "finrlx_worker.py"),
            )
        )
        self.source = Path(
            source
            or os.getenv(
                "FINRLX_SOURCE",
                "/home/botadmin/vendor/FinRL-Trading",
            )
        )
        self.source_commit = source_commit or os.getenv("FINRLX_SOURCE_COMMIT")
        if self.source_commit is None and (self.source / ".git").exists():
            resolved = subprocess.run(
                ["git", "-C", str(self.source), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if resolved.returncode == 0:
                self.source_commit = resolved.stdout.strip()
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict[str, Any]:
        available = (
            Path(self.python).is_file()
            and self.script.is_file()
            and self.source.is_dir()
        )
        return {
            "available": available,
            "isolated": True,
            "upstream": "AI4Finance-Foundation/FinRL-Trading",
            "execution_enabled": False,
            "live_mode": False,
        }

    def run(
        self,
        candidate: AICandidateRecord,
        dataset: HistoricalDataset,
        initial_cash: float = 100_000.0,
        transaction_cost: float = 0.0005,
    ) -> dict[str, Any]:
        if candidate.status != "completed" or candidate.result is None:
            raise ValueError("AI candidate must be completed before FinRL-X evaluation")
        predictions = candidate.result.get("oos_predictions")
        if not predictions:
            raise ValueError("AI candidate has no OOS predictions")
        if not self.health()["available"]:
            raise RuntimeError("FinRL-X research worker is not installed")

        job = {
            "candidate_id": candidate.candidate_id,
            "dataset_id": dataset.dataset_id,
            "symbol": dataset.symbol,
            "candles": [asdict(candle) for candle in dataset.candles],
            "oos_predictions": predictions,
            "initial_cash": initial_cash,
            "transaction_cost": transaction_cost,
            "finrlx_source": str(self.source),
            "finrlx_source_commit": self.source_commit,
        }
        with tempfile.TemporaryDirectory(prefix="trading-finrlx-") as temp:
            job_path = Path(temp) / "job.json"
            result_path = Path(temp) / "result.json"
            job_path.write_text(json.dumps(job, separators=(",", ":")), encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = str(self.source)
            completed = subprocess.run(
                [
                    self.python,
                    str(self.script),
                    "--job",
                    str(job_path),
                    "--result",
                    str(result_path),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env=env,
                cwd=self.source,
            )
            if completed.returncode != 0:
                tail = (completed.stderr or completed.stdout)[-2000:]
                raise RuntimeError(f"FinRL-X worker failed ({completed.returncode}): {tail}")
            result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("execution_enabled") is not False or result.get("live_mode") is not False:
            raise RuntimeError("FinRL-X worker violated research-only safety gate")
        return result
