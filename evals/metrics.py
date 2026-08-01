"""确定性的评估计分，不触碰模型、网络或数据库。"""

from dataclasses import dataclass

from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.application.recall_service import _profile_relevance
from memory_mcp.profiles import InvestmentResearchProfile

from evals.schema import (
    CandidateCase,
    EvaluationDataset,
    RecallCase,
    RelationCase,
    SafetyCase,
)

type Prediction = frozenset[str] | bool


@dataclass(frozen=True, slots=True)
class PrecisionRecall:
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 1.0


@dataclass(frozen=True, slots=True)
class CategoryResult:
    case_count: int
    evaluated_count: int
    failed_count: int

    @property
    def pass_rate(self) -> float | None:
        if not self.evaluated_count:
            return None
        return (self.evaluated_count - self.failed_count) / self.evaluated_count


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    dataset_version: str
    case_counts: dict[str, int]
    candidate: PrecisionRecall | None
    relation: PrecisionRecall | None
    recall_at_k: float
    safety_pass_rate: float
    categories: dict[str, CategoryResult]
    failed_case_ids: tuple[str, ...]
    thresholds_met: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_version": self.dataset_version,
            "case_counts": self.case_counts,
            "candidate": _precision_recall_dict(self.candidate),
            "relation": _precision_recall_dict(self.relation),
            "recall_at_k": round(self.recall_at_k, 6),
            "safety_pass_rate": round(self.safety_pass_rate, 6),
            "categories": {
                category: {
                    "case_count": result.case_count,
                    "evaluated_count": result.evaluated_count,
                    "failed_count": result.failed_count,
                    "pass_rate": (
                        round(result.pass_rate, 6)
                        if result.pass_rate is not None
                        else None
                    ),
                }
                for category, result in sorted(self.categories.items())
            },
            "failed_case_ids": self.failed_case_ids,
            "thresholds_met": self.thresholds_met,
        }


def evaluate_dataset(
    dataset: EvaluationDataset,
    *,
    model_predictions: dict[str, Prediction] | None = None,
) -> EvaluationReport:
    """对确定性任务以及可选的真实模型预测进行计分。"""

    predictions = model_predictions
    candidate_counts = [0, 0, 0]
    relation_counts = [0, 0, 0]
    recall_hits = 0
    recall_expected = 0
    safety_passed = 0
    safety_total = 0
    failed: list[str] = []
    evaluated_ids: set[str] = set()
    case_counts = {task: 0 for task in ("candidate", "relation", "recall", "safety")}

    for case in dataset.cases:
        case_counts[case.task] += 1
        if isinstance(case, CandidateCase | RelationCase):
            if predictions is None:
                continue
            if case.id not in predictions:
                raise ValueError(f"prediction is missing for {case.id}")
            predicted = predictions[case.id]
            if not isinstance(predicted, frozenset):
                raise ValueError(f"prediction for {case.id} must be a label set")
            evaluated_ids.add(case.id)
            counts = (
                candidate_counts if isinstance(case, CandidateCase) else relation_counts
            )
            _add_set_counts(counts, case.expected, predicted)
            if predicted != case.expected:
                failed.append(case.id)
        elif isinstance(case, RecallCase):
            evaluated_ids.add(case.id)
            predicted = _recall_labels(case)
            hits = len(case.expected & predicted)
            recall_hits += hits
            recall_expected += len(case.expected)
            if hits != len(case.expected):
                failed.append(case.id)
        elif isinstance(case, SafetyCase):
            evaluated_ids.add(case.id)
            predicted_blocked = bool(
                RegexSensitiveContentGuard().inspect(case.content).categories
            )
            safety_total += 1
            if predicted_blocked == case.expected_blocked:
                safety_passed += 1
            else:
                failed.append(case.id)

    candidate = PrecisionRecall(*candidate_counts) if predictions is not None else None
    relation = PrecisionRecall(*relation_counts) if predictions is not None else None
    recall_at_k = recall_hits / recall_expected if recall_expected else 1.0
    safety_pass_rate = safety_passed / safety_total if safety_total else 1.0
    thresholds = dataset.thresholds
    threshold_checks = [
        recall_at_k >= thresholds.recall_at_k,
        safety_pass_rate >= thresholds.safety_pass_rate,
    ]
    if candidate is not None and relation is not None:
        threshold_checks.extend(
            (
                candidate.precision >= thresholds.candidate_precision,
                candidate.recall >= thresholds.candidate_recall,
                relation.precision >= thresholds.relation_precision,
                relation.recall >= thresholds.relation_recall,
            )
        )
    thresholds_met = all(threshold_checks)
    failed_ids = frozenset(failed)
    category_counts: dict[str, list[int]] = {}
    for case in dataset.cases:
        counts = category_counts.setdefault(case.category, [0, 0, 0])
        counts[0] += 1
        if case.id in evaluated_ids:
            counts[1] += 1
        if case.id in failed_ids:
            counts[2] += 1
    return EvaluationReport(
        dataset_version=dataset.version,
        case_counts=case_counts,
        candidate=candidate,
        relation=relation,
        recall_at_k=recall_at_k,
        safety_pass_rate=safety_pass_rate,
        categories={
            category: CategoryResult(
                case_count=counts[0],
                evaluated_count=counts[1],
                failed_count=counts[2],
            )
            for category, counts in category_counts.items()
        },
        failed_case_ids=tuple(sorted(failed)),
        thresholds_met=thresholds_met,
    )


def _precision_recall_dict(metric: PrecisionRecall | None) -> dict[str, object] | None:
    if metric is None:
        return None
    return {
        "precision": round(metric.precision, 6),
        "recall": round(metric.recall, 6),
        "true_positive": metric.true_positive,
        "false_positive": metric.false_positive,
        "false_negative": metric.false_negative,
    }


def _add_set_counts(
    counts: list[int],
    expected: frozenset[str],
    predicted: frozenset[str],
) -> None:
    counts[0] += len(expected & predicted)
    counts[1] += len(predicted - expected)
    counts[2] += len(expected - predicted)


def _recall_labels(case: RecallCase) -> frozenset[str]:
    profile = InvestmentResearchProfile()
    ranked = sorted(
        case.corpus,
        key=lambda item: (
            _profile_relevance(
                case.query,
                " ".join((item.subject, item.memory_type, item.content)),
                item.memory_type,
                profile.recall_priorities,
                profile.recall_hints,
            ),
            item.label,
        ),
        reverse=True,
    )
    return frozenset(item.label for item in ranked[: case.top_k])
