#!/usr/bin/env python3
import argparse
import signal
import threading
import time
from dataclasses import asdict

from trading_platform import api


STOP = False


def _stop(*_):
    global STOP
    STOP = True


def dispatch(job):
    if job.kind == "experiment.run":
        record = api.experiment_store.require(job.target_id)
        dataset = api.dataset_store.load(record.spec.dataset_id)
        return asdict(api.worker_service.run(record, dataset))

    if job.kind == "ai.run":
        record = api.ai_candidate_store.require(job.target_id)
        dataset = api.dataset_store.load(record.spec.dataset_id)
        return asdict(api.ai_worker.run(record, dataset))

    if job.kind == "ai.finrlx":
        record = api.ai_candidate_store.require(job.target_id)
        dataset = api.dataset_store.load(record.spec.dataset_id)
        finrlx_result = api.finrlx_service.run(record, dataset)
        merged = dict(record.result or {})
        merged["finrlx_backtest"] = finrlx_result
        updated = api.ai_candidate_store.update(
            record.candidate_id,
            record.status,
            gate_status=record.gate_status,
            gate_reasons=record.gate_reasons,
            result=merged,
            error=record.error,
        )
        return asdict(updated)

    raise ValueError(f"unsupported job kind: {job.kind}")


def run_once(lease_seconds: int) -> bool:
    job = api.job_queue.claim(lease_seconds=lease_seconds)
    if job is None:
        return False

    heartbeat_stop = threading.Event()

    def heartbeat_loop():
        interval = max(5.0, lease_seconds / 3)
        while not heartbeat_stop.wait(interval):
            try:
                api.job_queue.heartbeat(job.job_id, lease_seconds=lease_seconds)
            except Exception:
                return

    heartbeat = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat.start()
    try:
        result = dispatch(job)
        heartbeat_stop.set()
        heartbeat.join(timeout=2)
        api.job_queue.complete(job.job_id, result)
        api.audit.record(
            "job_completed",
            {"job_id": job.job_id, "kind": job.kind, "target_id": job.target_id},
        )
    except Exception as exc:
        heartbeat_stop.set()
        heartbeat.join(timeout=2)
        updated = api.job_queue.fail(job.job_id, str(exc))
        api.audit.record(
            "job_failed",
            {
                "job_id": job.job_id,
                "kind": job.kind,
                "target_id": job.target_id,
                "status": updated.status,
                "attempts": updated.attempts,
                "error": str(exc)[-1000:],
            },
        )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--lease-seconds", type=int, default=300)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    recovered = api.job_queue.recover_stale()
    print(f"JOB_WORKER_READY recovered={recovered}", flush=True)

    if args.once:
        run_once(args.lease_seconds)
        return

    while not STOP:
        if not run_once(args.lease_seconds):
            time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    main()
