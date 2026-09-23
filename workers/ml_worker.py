#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path


FEATURE_VERSION = "direction-v1"
FEATURE_NAMES = (
    "return_1",
    "return_3",
    "return_5",
    "sma_3_over_5",
    "range_fraction",
    "volume_change",
)


def build_samples(candles):
    features = []
    targets = []
    forward_returns = []
    timestamps = []
    for i in range(5, len(candles) - 1):
        closes = [float(candles[j]["close"]) for j in range(i - 5, i + 1)]
        current = closes[-1]
        if current <= 0:
            continue
        prev_volume = float(candles[i - 1]["volume"])
        volume = float(candles[i]["volume"])
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        sma3 = sum(closes[-3:]) / 3
        sma5 = sum(closes[-5:]) / 5
        row = [
            current / closes[-2] - 1,
            current / closes[-4] - 1,
            current / closes[-6] - 1,
            sma3 / sma5 - 1,
            (high - low) / current,
            (volume / prev_volume - 1) if prev_volume > 0 else 0.0,
        ]
        next_close = float(candles[i + 1]["close"])
        features.append(row)
        targets.append(1 if next_close > current else 0)
        forward_returns.append(next_close / current - 1)
        timestamps.append(int(candles[i]["timestamp"]))
    return features, targets, forward_returns, timestamps


def walk_forward_slices(sample_count, folds, initial_train_fraction):
    if folds < 2:
        raise ValueError("folds must be at least 2")
    initial = max(20, int(sample_count * initial_train_fraction))
    remaining = sample_count - initial
    if remaining < folds:
        raise ValueError("not enough samples for requested walk-forward folds")
    test_size = max(1, remaining // folds)
    slices = []
    train_end = initial
    for fold in range(folds):
        test_start = train_end
        test_end = sample_count if fold == folds - 1 else min(sample_count, test_start + test_size)
        if test_end <= test_start:
            break
        slices.append((0, train_end, test_start, test_end))
        train_end = test_end
    return slices


def run(job):
    import numpy as np
    import sklearn
    from joblib import dump
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score

    features, targets, forward_returns, timestamps = build_samples(job["candles"])
    if len(features) < 30:
        raise ValueError("at least 30 supervised samples are required")
    folds = int(job.get("folds", 3))
    initial_train_fraction = float(job.get("initial_train_fraction", 0.5))
    slices = walk_forward_slices(len(features), folds, initial_train_fraction)
    x = np.asarray(features, dtype=float)
    y = np.asarray(targets, dtype=int)
    r = np.asarray(forward_returns, dtype=float)
    seed = int(job.get("seed", 42))
    hyper = dict(job.get("hyperparameters") or {})
    hyper.setdefault("n_estimators", 200)
    hyper.setdefault("max_depth", 5)
    hyper.setdefault("min_samples_leaf", 3)
    hyper.setdefault("class_weight", "balanced")
    hyper["random_state"] = seed
    hyper["n_jobs"] = 1

    fold_results = []
    all_predictions = []
    all_targets = []
    strategy_returns = []
    baseline_returns = []
    prediction_rows = []
    for fold_index, (train_start, train_end, test_start, test_end) in enumerate(slices):
        model = RandomForestClassifier(**hyper)
        model.fit(x[train_start:train_end], y[train_start:train_end])
        pred = model.predict(x[test_start:test_end])
        actual = y[test_start:test_end]
        accuracy = float(accuracy_score(actual, pred))
        majority = int(np.mean(y[train_start:train_end]) >= 0.5)
        baseline_accuracy = float(np.mean(actual == majority))
        fold_strategy = r[test_start:test_end] * pred
        fold_baseline = r[test_start:test_end]
        fold_results.append(
            {
                "fold": fold_index + 1,
                "train_samples": train_end - train_start,
                "test_samples": test_end - test_start,
                "test_start_timestamp": timestamps[test_start],
                "test_end_timestamp": timestamps[test_end - 1],
                "accuracy": accuracy,
                "baseline_accuracy": baseline_accuracy,
                "accuracy_uplift": accuracy - baseline_accuracy,
                "strategy_return_sum": float(np.sum(fold_strategy)),
                "buy_hold_return_sum": float(np.sum(fold_baseline)),
            }
        )
        all_predictions.extend(int(v) for v in pred)
        all_targets.extend(int(v) for v in actual)
        strategy_returns.extend(float(v) for v in fold_strategy)
        baseline_returns.extend(float(v) for v in fold_baseline)
        for offset, prediction in enumerate(pred):
            sample_index = test_start + offset
            prediction_rows.append(
                {
                    "timestamp": timestamps[sample_index],
                    "prediction": int(prediction),
                    "target": int(actual[offset]),
                    "forward_return": float(r[sample_index]),
                }
            )

    final_model = RandomForestClassifier(**hyper)
    final_model.fit(x, y)
    artifact_path = Path(job["model_artifact_path"])
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    dump(final_model, artifact_path)
    model_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()

    accuracies = np.asarray([fold["accuracy"] for fold in fold_results], dtype=float)
    uplifts = np.asarray([fold["accuracy_uplift"] for fold in fold_results], dtype=float)
    return {
        "worker": "sklearn-random-forest",
        "sklearn_version": sklearn.__version__,
        "dataset_id": job["dataset_id"],
        "model_family": "random_forest_direction",
        "feature_version": FEATURE_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "seed": seed,
        "hyperparameters": hyper,
        "sample_count": len(features),
        "fold_count": len(fold_results),
        "folds": fold_results,
        "mean_accuracy": float(np.mean(accuracies)),
        "accuracy_std": float(np.std(accuracies)),
        "mean_accuracy_uplift": float(np.mean(uplifts)),
        "out_of_sample_accuracy": float(np.mean(np.asarray(all_predictions) == np.asarray(all_targets))),
        "strategy_return_sum": float(np.sum(strategy_returns)),
        "buy_hold_return_sum": float(np.sum(baseline_returns)),
        "oos_predictions": prediction_rows,
        "model_artifact": str(artifact_path),
        "model_sha256": model_sha256,
        "live_mode": False,
        "execution_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    result = run(job)
    Path(args.result).write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
