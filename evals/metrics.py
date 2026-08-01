"""确定性的评估计分，不触碰模型、网络或数据库。"""

from dataclasses import dataclass

from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.application.recall_service import _text_relevance

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
class EvaluationReport:
    dataset_version: str
    case_counts: dict[str, int]
    candidate: PrecisionRecall
    relation: PrecisionRecall
    recall_at_k: float
    safety_pass_rate: float
    failed_case_ids: tuple[str, ...]
    thresholds_met: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_version": self.dataset_version,
            "case_counts": self.case_counts,
            "candidate": {
                "precision": round(self.candidate.precision, 6),
                "recall": round(self.candidate.recall, 6),
                "true_positive": self.candidate.true_positive,
                "false_positive": self.candidate.false_positive,
                "false_negative": self.candidate.false_negative,
            },
            "relation": {
                "precision": round(self.relation.precision, 6),
                "recall": round(self.relation.recall, 6),
                "true_positive": self.relation.true_positive,
                "false_positive": self.relation.false_positive,
                "false_negative": self.relation.false_negative,
            },
            "recall_at_k": round(self.recall_at_k, 6),
            "safety_pass_rate": round(self.safety_pass_rate, 6),
            "failed_case_ids": self.failed_case_ids,
            "thresholds_met": self.thresholds_met,
        }


def evaluate_dataset(
    dataset: EvaluationDataset,
    *,
    model_predictions: dict[str, Prediction] | None = None,
) -> EvaluationReport:
    """对同一组金标与离线基线或显式真实模型预测进行计分。"""

    predictions = model_predictions or {}
    candidate_counts = [0, 0, 0]
    relation_counts = [0, 0, 0]
    recall_hits = 0
    recall_expected = 0
    safety_passed = 0
    safety_total = 0
    failed: list[str] = []
    case_counts = {task: 0 for task in ("candidate", "relation", "recall", "safety")}

    for case in dataset.cases:
        case_counts[case.task] += 1
        if isinstance(case, CandidateCase | RelationCase):
            predicted = predictions.get(case.id, case.baseline)
            if not isinstance(predicted, frozenset):
                raise ValueError(f"prediction for {case.id} must be a label set")
            counts = (
                candidate_counts if isinstance(case, CandidateCase) else relation_counts
            )
            _add_set_counts(counts, case.expected, predicted)
            if predicted != case.expected:
                failed.append(case.id)
        elif isinstance(case, RecallCase):
            predicted = _recall_labels(case)
            hits = len(case.expected & predicted)
            recall_hits += hits
            recall_expected += len(case.expected)
            if hits != len(case.expected):
                failed.append(case.id)
        elif isinstance(case, SafetyCase):
            predicted_blocked = bool(
                RegexSensitiveContentGuard().inspect(case.content).categories
            )
            safety_total += 1
            if predicted_blocked == case.expected_blocked:
                safety_passed += 1
            else:
                failed.append(case.id)

    candidate = PrecisionRecall(*candidate_counts)
    relation = PrecisionRecall(*relation_counts)
    recall_at_k = recall_hits / recall_expected if recall_expected else 1.0
    safety_pass_rate = safety_passed / safety_total if safety_total else 1.0
    thresholds = dataset.thresholds
    thresholds_met = all(
        (
            candidate.precision >= thresholds.candidate_precision,
            candidate.recall >= thresholds.candidate_recall,
            relation.precision >= thresholds.relation_precision,
            relation.recall >= thresholds.relation_recall,
            recall_at_k >= thresholds.recall_at_k,
            safety_pass_rate >= thresholds.safety_pass_rate,
        )
    )
    return EvaluationReport(
        dataset_version=dataset.version,
        case_counts=case_counts,
        candidate=candidate,
        relation=relation,
        recall_at_k=recall_at_k,
        safety_pass_rate=safety_pass_rate,
        failed_case_ids=tuple(sorted(failed)),
        thresholds_met=thresholds_met,
    )


def _add_set_counts(
    counts: list[int],
    expected: frozenset[str],
    predicted: frozenset[str],
) -> None:
    counts[0] += len(expected & predicted)
    counts[1] += len(predicted - expected)
    counts[2] += len(expected - predicted)


def _recall_labels(case: RecallCase) -> frozenset[str]:
    ranked = sorted(
        case.corpus,
        key=lambda item: (
            _text_relevance(
                case.query,
                " ".join((item.subject, item.memory_type, item.content)),
            ),
            item.label,
        ),
        reverse=True,
    )
    return frozenset(item.label for item in ranked[: case.top_k])
