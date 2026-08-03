"""确定性的评估计分，不触碰模型、网络或数据库。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from memory_mcp.core import (
    AssertionKind,
    Evidence,
    LifecycleStatus,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    MessageRole,
    PrincipalContext,
    RecallQuery,
    SensitivityLevel,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.composition import create_memory_service
from memory_mcp.profiles import InvestmentResearchProfile

from evals.schema import (
    CandidateCase,
    EvaluationDataset,
    RecallCase,
    RecallCorpusItem,
    RelationCase,
    SafetyCase,
)

type Prediction = frozenset[str] | bool

_RECALL_OWNER = PrincipalContext("evaluation-owner")
_RECALL_TIME = datetime(2020, 1, 1, tzinfo=UTC)


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
            if hits != len(case.expected) or (not case.expected and predicted):
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
    repository = InMemoryMemoryRepository()
    service = create_memory_service(
        repository,
        [profile],
        recall_candidate_limit=case.candidate_limit,
    )
    labels_by_id = {}
    for item in case.corpus:
        record = _recall_record(case, item)
        repository.add(_RECALL_OWNER, record)
        labels_by_id[record.item.memory_id] = item.label
    result = service.recall_memory(
        _RECALL_OWNER,
        RecallQuery(
            profile_id=profile.profile_id,
            query=case.query,
            max_items=case.top_k,
            token_budget=8_000,
        ),
    )
    return frozenset(labels_by_id[item.memory_id] for item in result.items)


def _recall_record(case: RecallCase, item: RecallCorpusItem) -> MemoryRecord:
    memory_id = uuid5(NAMESPACE_URL, f"recall-memory:{case.id}:{item.label}")
    revision_id = uuid5(NAMESPACE_URL, f"recall-revision:{case.id}:{item.label}")
    evidence_id = uuid5(NAMESPACE_URL, f"recall-evidence:{case.id}:{item.label}")
    observed_at = _RECALL_TIME - timedelta(days=item.observed_days_ago)
    return MemoryRecord(
        item=MemoryItem(
            memory_id=memory_id,
            owner_id=_RECALL_OWNER.owner_id,
            profile_id="investment-research",
            subject=item.subject,
            memory_type=item.memory_type,
            created_at=observed_at,
        ),
        current_revision=MemoryRevision(
            revision_id=revision_id,
            memory_id=memory_id,
            owner_id=_RECALL_OWNER.owner_id,
            revision_number=1,
            content=item.content,
            assertion_kind=AssertionKind.USER_VIEW,
            lifecycle_status=LifecycleStatus.ACTIVE,
            business_progress=None,
            save_rationale="evaluation fixture",
            observed_at=observed_at,
            created_at=observed_at,
            extraction_confidence=1.0,
            verification_status=VerificationStatus.USER_ASSERTED,
            sensitivity_level=SensitivityLevel.INTERNAL,
            valid_from=observed_at,
            valid_until=None,
        ),
        evidence=(
            Evidence(
                evidence_id=evidence_id,
                memory_id=memory_id,
                revision_id=revision_id,
                owner_id=_RECALL_OWNER.owner_id,
                conversation_id=f"eval-{case.id}",
                source_turn_id="fixture",
                source_expression=item.content,
                observed_at=observed_at,
                created_at=observed_at,
                source_role=MessageRole.USER,
            ),
        ),
    )
