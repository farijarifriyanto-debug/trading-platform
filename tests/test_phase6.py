import pytest

from trading_platform.ai_research import (
    AICandidateSpec,
    AICandidateStore,
    RobustnessPolicy,
    evaluate_robustness,
)


def _result(**overrides):
    result = {
        "sample_count": 200,
        "fold_count": 4,
        "mean_accuracy": 0.56,
        "mean_accuracy_uplift": 0.03,
        "accuracy_std": 0.04,
    }
    result.update(overrides)
    return result


def test_robustness_gate_passes_only_research_thresholds():
    status, reasons = evaluate_robustness(_result(), RobustnessPolicy())

    assert status == "RESEARCH_PASS"
    assert reasons == ()


def test_robustness_gate_requires_review_with_explicit_reasons():
    status, reasons = evaluate_robustness(
        _result(sample_count=50, mean_accuracy=0.49, accuracy_std=0.3),
        RobustnessPolicy(),
    )

    assert status == "REVIEW_REQUIRED"
    assert "sample_count<100" in reasons
    assert "mean_accuracy<0.52" in reasons
    assert "accuracy_std>0.15" in reasons


def test_ai_candidate_store_is_reproducible_and_never_implies_execution(tmp_path):
    store = AICandidateStore(tmp_path / "candidates")
    spec = AICandidateSpec(
        dataset_id="a" * 64,
        seed=42,
        folds=3,
        hyperparameters={"n_estimators": 100},
    )

    first = store.create(spec)
    second = store.create(spec)
    completed = store.update(
        first.candidate_id,
        "completed",
        gate_status="RESEARCH_PASS",
        result={
            **_result(),
            "live_mode": False,
            "execution_enabled": False,
            "model_sha256": "b" * 64,
        },
    )

    assert first.candidate_id == second.candidate_id
    assert completed.gate_status == "RESEARCH_PASS"
    assert completed.result["execution_enabled"] is False
    assert store.require(first.candidate_id).result["live_mode"] is False


def test_ai_candidate_store_rejects_path_traversal(tmp_path):
    store = AICandidateStore(tmp_path / "candidates")
    with pytest.raises(ValueError, match="SHA-256"):
        store.get("../../etc/passwd")


def test_finrlx_service_health_is_research_only(tmp_path):
    from trading_platform.finrlx import FinRLXResearchService

    service = FinRLXResearchService(
        python="/missing/python",
        script=tmp_path / "missing-worker.py",
        source=tmp_path / "missing-source",
    )
    health = service.health()

    assert health["available"] is False
    assert health["execution_enabled"] is False
    assert health["live_mode"] is False
    assert health["upstream"] == "AI4Finance-Foundation/FinRL-Trading"
