"""确定性纯函数评测计分。

所有指标计算为纯函数，不触碰模型、网络或数据库。
Runner 负责编排，这里只做数学。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from evals.schema import (
    CandidateCase,
    EvaluationDataset,
    IsolationCase,
    LifecycleCase,
    RecallCase,
    RelationCase,
    SafetyCase,
)

type Prediction = frozenset[str] | bool


# ── 基础指标 ──


@dataclass(frozen=True, slots=True)
class PrecisionRecall:
    """集合级 precision/recall/F1。"""

    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass(frozen=True, slots=True)
class CategoryResult:
    """分类汇总。"""

    case_count: int
    evaluated_count: int
    failed_count: int

    @property
    def pass_rate(self) -> float | None:
        if not self.evaluated_count:
            return None
        return (self.evaluated_count - self.failed_count) / self.evaluated_count


@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    """决策级混淆矩阵。

    rows=expected, cols=predicted; 支持 multi-label。
    """

    labels: tuple[str, ...]
    matrix: tuple[tuple[int, ...], ...]


# ── 集合匹配 ──


def set_precision_recall(
    expected: frozenset[str],
    predicted: frozenset[str],
) -> PrecisionRecall:
    """计算两个标签集合的 TP/FP/FN。"""

    return PrecisionRecall(
        true_positive=len(expected & predicted),
        false_positive=len(predicted - expected),
        false_negative=len(expected - predicted),
    )


def aggregate_precision_recall(
    pairs: list[tuple[frozenset[str], frozenset[str]]],
) -> PrecisionRecall:
    """聚合多组 (expected, predicted) 的 TP/FP/FN。"""

    tp = sum(len(e & p) for e, p in pairs)
    fp = sum(len(p - e) for e, p in pairs)
    fn = sum(len(e - p) for e, p in pairs)
    return PrecisionRecall(tp, fp, fn)


# ── Recall@K / Precision@K / MRR ──


def recall_at_k(
    expected: frozenset[str],
    retrieved: tuple[str, ...],
    k: int | None = None,
) -> float:
    """Recall@K = |expected ∩ topK| / |expected|。"""

    if not expected:
        return 1.0 if not retrieved[: k or len(retrieved)] else 0.0
    top = retrieved[: k or len(retrieved)]
    hits = len(expected & frozenset(top))
    return hits / len(expected)


def precision_at_k(
    expected: frozenset[str],
    retrieved: tuple[str, ...],
    k: int | None = None,
) -> float:
    """Precision@K = |expected ∩ topK| / |topK|。"""

    top = retrieved[: k or len(retrieved)]
    if not top:
        return 1.0
    return len(expected & frozenset(top)) / len(top)


def mean_reciprocal_rank(
    expected: frozenset[str],
    retrieved: tuple[str, ...],
) -> float:
    """MRR = 1/rank(first relevant)，无相关返回 0。"""

    for i, item in enumerate(retrieved):
        if item in expected:
            return 1.0 / (i + 1)
    return 0.0


# ── 混淆矩阵 ──


def confusion_matrix(
    expected: frozenset[str],
    predicted: frozenset[str],
    labels: tuple[str, ...],
) -> ConfusionMatrix:
    """多标签混淆矩阵：行=expected，列=predicted。"""

    matrix: list[list[int]] = [[0] * len(labels) for _ in labels]
    label_idx = {label: i for i, label in enumerate(labels)}
    for label in labels:
        e = label in expected
        p = label in predicted
        if e and p:
            matrix[label_idx[label]][label_idx[label]] += 1
        elif e and not p:
            for j in range(len(labels)):
                if j != label_idx[label]:
                    matrix[label_idx[label]][j] += 0
        elif not e and p:
            for i in range(len(labels)):
                if i != label_idx[label]:
                    matrix[i][label_idx[label]] += 0
    # 简化：只记 diagonal + off-diagonal as is
    for i, l_e in enumerate(labels):
        for j, l_p in enumerate(labels):
            if i == j:
                continue
            if l_e in expected and l_p in predicted and l_e != l_p:
                matrix[i][j] += 1
    return ConfusionMatrix(
        labels=labels,
        matrix=tuple(tuple(row) for row in matrix),
    )


# ── 报告 ──


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """单个 suite 的聚合结果。"""

    suite: str
    case_count: int
    evaluated_count: int
    failed_count: int
    failed_case_ids: tuple[str, ...] = ()

    @property
    def pass_rate(self) -> float | None:
        if not self.evaluated_count:
            return None
        return (self.evaluated_count - self.failed_count) / self.evaluated_count


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """完整评测报告。"""

    dataset_version: str
    mode: str
    case_counts: dict[str, int]
    suite_results: tuple[SuiteResult, ...]
    candidate: PrecisionRecall | None = None
    relation: PrecisionRecall | None = None
    recall_at_k: float = 1.0
    precision_at_k: float = 1.0
    mrr: float = 1.0
    safety_pass_rate: float = 1.0
    isolation_pass_rate: float = 1.0
    lifecycle_pass_rate: float = 1.0
    failed_case_ids: tuple[str, ...] = ()
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    thresholds_met: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_version": self.dataset_version,
            "mode": self.mode,
            "case_counts": self.case_counts,
            "suite_results": [
                {
                    "suite": sr.suite,
                    "case_count": sr.case_count,
                    "evaluated_count": sr.evaluated_count,
                    "failed_count": sr.failed_count,
                    "pass_rate": (
                        round(sr.pass_rate, 6) if sr.pass_rate is not None else None
                    ),
                    "failed_case_ids": list(sr.failed_case_ids),
                }
                for sr in self.suite_results
            ],
            "candidate": _pr_dict(self.candidate),
            "relation": _pr_dict(self.relation),
            "recall_at_k": round(self.recall_at_k, 6),
            "precision_at_k": round(self.precision_at_k, 6),
            "mrr": round(self.mrr, 6),
            "safety_pass_rate": round(self.safety_pass_rate, 6),
            "isolation_pass_rate": round(self.isolation_pass_rate, 6),
            "lifecycle_pass_rate": round(self.lifecycle_pass_rate, 6),
            "failed_case_ids": list(self.failed_case_ids),
            "skipped_reasons": dict(self.skipped_reasons),
            "thresholds_met": self.thresholds_met,
        }


def _pr_dict(metric: PrecisionRecall | None) -> dict[str, object] | None:
    if metric is None:
        return None
    return {
        "precision": round(metric.precision, 6),
        "recall": round(metric.recall, 6),
        "f1": round(metric.f1, 6),
        "true_positive": metric.true_positive,
        "false_positive": metric.false_positive,
        "false_negative": metric.false_negative,
    }


def evaluate_dataset(
    dataset: EvaluationDataset,
    *,
    mode: str = "deterministic",
    model_predictions: dict[str, Prediction] | None = None,
    recall_predictions: dict[str, tuple[str, ...]] | None = None,
    skipped_reasons: dict[str, str] | None = None,
) -> EvaluationReport:
    """对数据集计分。

    - deterministic：recall + safety + isolation + lifecycle 用生产代码直接评估；
    - live-extraction：candidate + relation 使用 model_predictions；
    - live-embedding：recall 使用 recall_predictions（可比较 lexical vs vector）。
    """

    _skipped = skipped_reasons or {}
    failed: list[str] = []
    evaluated_ids: set[str] = set()
    case_counts: dict[str, int] = Counter()
    suite_counts: dict[str, list[int]] = {}  # [total, evaluated, failed]

    candidate_pairs: list[tuple[frozenset[str], frozenset[str]]] = []
    relation_pairs: list[tuple[frozenset[str], frozenset[str]]] = []
    recall_hits = 0
    recall_expected_total = 0
    recall_at_k_values: list[float] = []
    precision_at_k_values: list[float] = []
    mrr_values: list[float] = []
    safety_passed = 0
    safety_total = 0
    isolation_passed = 0
    isolation_total = 0
    lifecycle_passed = 0
    lifecycle_total = 0

    for case in dataset.cases:
        case_counts[case.task] += 1
        suite_counts.setdefault(case.suite, [0, 0, 0])
        suite_counts[case.suite][0] += 1

        # 按模式决定是否评估
        should_evaluate = True
        if isinstance(case, CandidateCase | RelationCase):
            if mode == "deterministic":
                should_evaluate = False
                _skipped[case.id] = "deterministic mode skips extraction cases"
            elif model_predictions is None or case.id not in model_predictions:
                _skipped[case.id] = "no model prediction available"
                should_evaluate = False
        elif isinstance(case, RecallCase):
            if mode == "live-embedding":
                if recall_predictions is None or case.id not in recall_predictions:
                    _skipped[case.id] = "no embedding prediction available"
                    should_evaluate = False
            # deterministic and live-extraction evaluate recall directly
        elif isinstance(case, SafetyCase):
            pass  # Safety 在所有模式都评估（RegexSensitiveContentGuard）
        elif isinstance(case, IsolationCase):
            # owner 隔离需要真实 PrincipalContext + Repository 跨 owner 验证，
            # 当前三种模式均无此能力，诚实跳过而非假评估。
            should_evaluate = False
            _skipped[case.id] = (
                "isolation requires live owner-scoped repository verification"
            )
        elif isinstance(case, LifecycleCase):
            # 生命周期状态转换需真实 capture 流转（duplicate/replacement/ambiguous/
            # revoke/expire），当前三种模式均无此能力，诚实跳过。
            should_evaluate = False
            _skipped[case.id] = (
                "lifecycle requires live capture flow for state transitions"
            )

        if not should_evaluate:
            continue

        evaluated_ids.add(case.id)
        suite_counts[case.suite][1] += 1

        if isinstance(case, CandidateCase | RelationCase):
            assert model_predictions is not None
            predicted = model_predictions[case.id]
            if not isinstance(predicted, frozenset):
                raise ValueError(f"prediction for {case.id} must be a label set")
            pair = (case.expected, predicted)
            if isinstance(case, CandidateCase):
                candidate_pairs.append(pair)
            else:
                relation_pairs.append(pair)
            if predicted != case.expected:
                failed.append(case.id)
        elif isinstance(case, RecallCase):
            if recall_predictions and case.id in recall_predictions:
                retrieved = recall_predictions[case.id]
            else:
                retrieved = _recall_labels(case)
            hits = len(case.expected & frozenset(retrieved))
            recall_hits += hits
            recall_expected_total += len(case.expected)
            rk = recall_at_k(case.expected, retrieved, case.top_k)
            pk = precision_at_k(case.expected, retrieved, case.top_k)
            mrr = mean_reciprocal_rank(case.expected, retrieved)
            recall_at_k_values.append(rk)
            precision_at_k_values.append(pk)
            mrr_values.append(mrr)
            if hits != len(case.expected) or (not case.expected and retrieved):
                failed.append(case.id)
        elif isinstance(case, SafetyCase):
            from memory_mcp.core.adapters.sensitive import (
                RegexSensitiveContentGuard,
            )

            predicted_blocked = bool(
                RegexSensitiveContentGuard().inspect(case.content).categories
            )
            safety_total += 1
            if predicted_blocked == case.expected_blocked:
                safety_passed += 1
            else:
                failed.append(case.id)
        # IsolationCase / LifecycleCase 在上方按模式跳过，不在此评估；
        # 真实 owner 隔离与生命周期流转需专门 live 模式，当前诚实跳过。

    candidate = (
        aggregate_precision_recall(candidate_pairs) if candidate_pairs else None
    )
    relation = (
        aggregate_precision_recall(relation_pairs) if relation_pairs else None
    )
    avg_recall_at_k = (
        sum(recall_at_k_values) / len(recall_at_k_values)
        if recall_at_k_values
        else (recall_hits / recall_expected_total if recall_expected_total else 1.0)
    )
    avg_precision_at_k = (
        sum(precision_at_k_values) / len(precision_at_k_values)
        if precision_at_k_values
        else 1.0
    )
    avg_mrr = sum(mrr_values) / len(mrr_values) if mrr_values else 1.0
    safety_pass_rate = (
        safety_passed / safety_total if safety_total else 1.0
    )
    isolation_pass_rate = (
        isolation_passed / isolation_total if isolation_total else 1.0
    )
    lifecycle_pass_rate = (
        lifecycle_passed / lifecycle_total if lifecycle_total else 1.0
    )

    thresholds = dataset.thresholds
    threshold_checks = [
        avg_recall_at_k >= thresholds.recall_at_k,
        avg_precision_at_k >= thresholds.precision_at_k,
        avg_mrr >= thresholds.mrr,
        safety_pass_rate >= thresholds.safety_pass_rate,
    ]
    # isolation/lifecycle 仅在确有评估 case 时才门禁；无评估 case 时跳过该检查，
    # 避免恒真的假绿门禁掩盖回归。
    if isolation_total:
        threshold_checks.append(
            isolation_pass_rate >= thresholds.isolation_pass_rate
        )
    if lifecycle_total:
        threshold_checks.append(
            lifecycle_pass_rate >= thresholds.lifecycle_pass_rate
        )
    if candidate is not None:
        threshold_checks.extend(
            (
                candidate.precision >= thresholds.candidate_precision,
                candidate.recall >= thresholds.candidate_recall,
            )
        )
    if relation is not None:
        threshold_checks.extend(
            (
                relation.precision >= thresholds.relation_precision,
                relation.recall >= thresholds.relation_recall,
            )
        )
    thresholds_met = all(threshold_checks)

    failed_set = frozenset(failed)
    suite_results = tuple(
        SuiteResult(
            suite=suite,
            case_count=counts[0],
            evaluated_count=counts[1],
            failed_count=counts[2],
            failed_case_ids=tuple(
                sorted(
                    cid
                    for cid in failed_set
                    for c in dataset.cases
                    if c.id == cid and c.suite == suite
                )
            ),
        )
        for suite, counts in sorted(suite_counts.items())
    )

    return EvaluationReport(
        dataset_version=dataset.version,
        mode=mode,
        case_counts=dict(case_counts),
        suite_results=suite_results,
        candidate=candidate,
        relation=relation,
        recall_at_k=avg_recall_at_k,
        precision_at_k=avg_precision_at_k,
        mrr=avg_mrr,
        safety_pass_rate=safety_pass_rate,
        isolation_pass_rate=isolation_pass_rate,
        lifecycle_pass_rate=lifecycle_pass_rate,
        failed_case_ids=tuple(sorted(failed)),
        skipped_reasons=_skipped,
        thresholds_met=thresholds_met,
    )


# ── Recall 评估（确定性，调用生产代码）──


def _recall_labels(case: RecallCase) -> tuple[str, ...]:
    """通过生产 RecallService 评估一条召回案例，返回按相关性排序的 label。"""

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
    from memory_mcp.core.composition import create_memory_service
    from memory_mcp.profiles import (
        GeneralWorkProfile,
        InvestmentResearchProfile,
    )

    _owner = PrincipalContext("evaluation-owner")
    _now = datetime(2026, 8, 1, 10, tzinfo=UTC)
    _profiles = {
        "general-work": GeneralWorkProfile,
        "investment-research": InvestmentResearchProfile,
    }
    profile_cls = _profiles.get(case.profile_id, InvestmentResearchProfile)
    profile = profile_cls()
    repository = InMemoryMemoryRepository()
    service = create_memory_service(
        repository,
        [profile],
        recall_candidate_limit=case.candidate_limit,
    )
    labels_by_id: dict = {}
    for item in case.corpus:
        memory_id = uuid5(NAMESPACE_URL, f"recall:{case.id}:{item.label}")
        revision_id = uuid5(NAMESPACE_URL, f"recall-rev:{case.id}:{item.label}")
        evidence_id = uuid5(NAMESPACE_URL, f"recall-evd:{case.id}:{item.label}")
        observed_at = _now - timedelta(days=item.observed_days_ago)
        repository.add(
            _owner,
            MemoryRecord(
                item=MemoryItem(
                    memory_id=memory_id,
                    owner_id=_owner.owner_id,
                    profile_id=case.profile_id,
                    subject=item.subject,
                    memory_type=item.memory_type,
                    created_at=observed_at,
                ),
                current_revision=MemoryRevision(
                    revision_id=revision_id,
                    memory_id=memory_id,
                    owner_id=_owner.owner_id,
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
                        owner_id=_owner.owner_id,
                        conversation_id=f"eval-{case.id}",
                        source_turn_id="fixture",
                        source_expression=item.content,
                        observed_at=observed_at,
                        created_at=observed_at,
                        source_role=MessageRole.USER,
                    ),
                ),
            ),
        )
        labels_by_id[memory_id] = item.label
    result = service.recall_memory(
        _owner,
        RecallQuery(
            profile_id=case.profile_id,
            query=case.query,
            max_items=case.top_k,
            token_budget=case.token_budget,
        ),
    )
    return tuple(
        labels_by_id.get(item.memory_id, f"unknown:{item.memory_id}")
        for item in result.items
    )
