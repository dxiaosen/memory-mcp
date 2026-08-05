"""评测体系回归测试：schema/metrics/matching/baseline/runner/deterministic 一致性。"""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.metrics import (
    PrecisionRecall,
    aggregate_precision_recall,
    confusion_matrix,
    evaluate_dataset,
    mean_reciprocal_rank,
    precision_at_k,
    recall_at_k,
    set_precision_recall,
)
from evals.runner import _compare_with_baseline, _filter_dataset, _run_payload, main
from evals.schema import load_dataset

_DATASET = Path("evals/cases.json")


# ── Schema 校验 ──


def test_dataset_has_unique_case_ids() -> None:
    dataset = load_dataset(_DATASET)
    ids = [c.id for c in dataset.cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"


def test_dataset_rejects_extra_fields(tmp_path: Path) -> None:
    raw = json.loads(_DATASET.read_text(encoding="utf-8"))
    raw["cases"][0]["owner_id"] = "forged-owner"
    target = tmp_path / "invalid.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_dataset(target)


def test_dataset_all_cases_have_required_fields() -> None:
    dataset = load_dataset(_DATASET)
    for case in dataset.cases:
        assert case.suite
        assert case.mode
        assert case.profile_id
        assert case.rationale


# ── 指标纯函数 ──


def test_set_precision_recall() -> None:
    pr = set_precision_recall(
        frozenset({"a", "b", "c"}),
        frozenset({"a", "c", "d"}),
    )
    assert pr.true_positive == 2
    assert pr.false_positive == 1
    assert pr.false_negative == 1
    assert pr.precision == 2 / 3
    assert pr.recall == 2 / 3


def test_precision_recall_f1() -> None:
    pr = PrecisionRecall(true_positive=1, false_positive=1, false_negative=1)
    assert pr.f1 == pytest.approx(0.5, abs=0.01)


def test_recall_at_k() -> None:
    expected = frozenset({"a", "c"})
    retrieved = ("a", "b", "c", "d")
    assert recall_at_k(expected, retrieved, k=2) == 0.5
    assert recall_at_k(expected, retrieved, k=4) == 1.0


def test_precision_at_k() -> None:
    expected = frozenset({"a", "c"})
    retrieved = ("a", "b", "c", "d")
    assert precision_at_k(expected, retrieved, k=2) == 0.5
    assert precision_at_k(expected, retrieved, k=1) == 1.0


def test_mean_reciprocal_rank() -> None:
    assert mean_reciprocal_rank(frozenset({"c"}), ("a", "b", "c")) == pytest.approx(1 / 3)
    assert mean_reciprocal_rank(frozenset({"z"}), ("a", "b", "c")) == 0.0


def test_aggregate_precision_recall() -> None:
    pairs = [
        (frozenset({"a"}), frozenset({"a"})),
        (frozenset({"b"}), frozenset({"c"})),
    ]
    pr = aggregate_precision_recall(pairs)
    assert pr.true_positive == 1
    assert pr.false_positive == 1
    assert pr.false_negative == 1


def test_confusion_matrix_diagonal() -> None:
    cm = confusion_matrix(
        frozenset({"a"}),
        frozenset({"a"}),
        ("a", "b"),
    )
    assert cm.matrix[0][0] == 1


# ── Deterministic 一致性 ──


def test_deterministic_repeatable() -> None:
    dataset = load_dataset(_DATASET)
    r1 = evaluate_dataset(dataset, mode="deterministic")
    r2 = evaluate_dataset(dataset, mode="deterministic")
    assert r1.recall_at_k == r2.recall_at_k
    assert r1.safety_pass_rate == r2.safety_pass_rate
    assert r1.failed_case_ids == r2.failed_case_ids


def test_deterministic_skips_extraction() -> None:
    dataset = load_dataset(_DATASET)
    report = evaluate_dataset(dataset, mode="deterministic")
    assert report.candidate is None
    assert report.relation is None
    assert report.recall_at_k == 1.0
    assert report.safety_pass_rate == 1.0


def test_deterministic_thresholds_met() -> None:
    dataset = load_dataset(_DATASET)
    report = evaluate_dataset(dataset, mode="deterministic")
    assert report.thresholds_met


# ── Provider 未配置时正确 skipped ──


def test_live_extraction_without_provider_skips() -> None:
    dataset = load_dataset(_DATASET)
    report = evaluate_dataset(
        dataset,
        mode="live-extraction",
        model_predictions=None,
        skipped_reasons={},
    )
    assert report.candidate is None
    assert report.relation is None


# ── 筛选 ──


def test_filter_by_suite() -> None:
    dataset = load_dataset(_DATASET)
    filtered = _filter_dataset(dataset, suite="recall")
    assert all(c.suite == "recall" for c in filtered.cases)
    assert len(filtered.cases) > 0


def test_filter_by_tag() -> None:
    dataset = load_dataset(_DATASET)
    first_tag = dataset.cases[0].tags[0] if dataset.cases[0].tags else None
    if first_tag:
        filtered = _filter_dataset(dataset, tag=first_tag)
        assert all(first_tag in c.tags for c in filtered.cases)


# ── Baseline ──


def test_baseline_comparison_no_regression() -> None:
    from evals.metrics import EvaluationReport

    report = EvaluationReport(
        dataset_version="test",
        mode="deterministic",
        case_counts={},
        suite_results=(),
        recall_at_k=1.0,
        safety_pass_rate=1.0,
    )
    baseline = {"recall_at_k": 1.0, "safety_pass_rate": 1.0}
    result = _compare_with_baseline(report, baseline)
    assert result["regression"] is False


def test_baseline_comparison_detects_regression() -> None:
    from evals.metrics import EvaluationReport

    report = EvaluationReport(
        dataset_version="test",
        mode="deterministic",
        case_counts={},
        suite_results=(),
        recall_at_k=0.8,
        safety_pass_rate=1.0,
    )
    baseline = {"recall_at_k": 1.0, "safety_pass_rate": 1.0}
    result = _compare_with_baseline(report, baseline)
    assert result["regression"] is True
    assert "recall_at_k" in result["regressed_metrics"]


# ── 回归退出码 ──


def test_deterministic_returns_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["evals.runner", "--mode", "deterministic"],
    )
    exit_code = main()
    assert exit_code == 0


# ── 输出安全 ──


def test_report_does_not_contain_secrets() -> None:
    dataset = load_dataset(_DATASET)
    payload = _run_payload(dataset, dataset_path=_DATASET, mode="deterministic")
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "Bearer " not in rendered
    assert "postgresql://" not in rendered
    assert '"sk-' not in rendered  # API key prefix as JSON string value
    assert "以后写公司深度报告时" not in rendered
