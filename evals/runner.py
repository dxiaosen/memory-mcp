"""命令行评估入口，支持三种模式、筛选、baseline 和回归门禁。

用法：
    uv run python -m evals.runner --mode deterministic
    uv run python -m evals.runner --mode live-extraction
    uv run python -m evals.runner --mode live-embedding
"""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from evals.metrics import EvaluationReport, evaluate_dataset
from evals.schema import (
    CandidateCase,
    EvaluationDataset,
    RelationCase,
    load_dataset,
)

_DEFAULT_DATASET = Path(__file__).with_name("cases.json")
_BASELINE_DIR = Path(__file__).with_name("baselines")


def run_evaluation(
    dataset: EvaluationDataset,
    *,
    mode: str = "deterministic",
    model_predictions: dict[str, Any] | None = None,
    recall_predictions: dict[str, tuple[str, ...]] | None = None,
) -> EvaluationReport:
    """按模式评估数据集。"""

    return evaluate_dataset(
        dataset,
        mode=mode,
        model_predictions=model_predictions,
        recall_predictions=recall_predictions,
    )


def _filter_dataset(
    dataset: EvaluationDataset,
    *,
    suite: str | None = None,
    tag: str | None = None,
    case_id: str | None = None,
) -> EvaluationDataset:
    """按 suite/tag/case_id 过滤数据集。"""

    cases = dataset.cases
    if suite:
        cases = [c for c in cases if c.suite == suite]
    if tag:
        cases = [c for c in cases if tag in c.tags]
    if case_id:
        cases = [c for c in cases if c.id == case_id]
    return dataset.model_copy(update={"cases": tuple(cases)})


def _git_commit() -> str | None:
    """获取当前 git commit（用于报告溯源）。"""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _load_baseline(mode: str) -> dict[str, Any] | None:
    """加载对应模式的 baseline。"""

    path = _BASELINE_DIR / f"{mode}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _compare_with_baseline(
    report: EvaluationReport,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    """与 baseline 比较关键指标。"""

    if baseline is None:
        return {"has_baseline": False, "regression": False}
    changes: dict[str, Any] = {}
    regressions: list[str] = []
    for key in (
        "recall_at_k",
        "precision_at_k",
        "mrr",
        "safety_pass_rate",
        "isolation_pass_rate",
        "lifecycle_pass_rate",
    ):
        old = baseline.get(key)
        new = getattr(report, key)
        if old is not None:
            changes[key] = {"baseline": old, "current": round(new, 6), "delta": round(new - old, 6)}
            if new < old:
                regressions.append(key)
    return {"has_baseline": True, "changes": changes, "regression": bool(regressions), "regressed_metrics": regressions}


def _run_payload(
    dataset: EvaluationDataset,
    *,
    dataset_path: Path,
    mode: str,
    suite: str | None = None,
    tag: str | None = None,
    case_id: str | None = None,
) -> dict[str, Any]:
    """执行评测并返回完整 payload。"""

    started_at = datetime.now(UTC)
    started = perf_counter()

    model_predictions: dict[str, Any] | None = None
    recall_predictions: dict[str, tuple[str, ...]] | None = None
    model_id = None

    if mode == "live-extraction":
        from memory_mcp.extraction.factory import create_configured_extractors
        from memory_mcp.extraction.settings import ExtractionSettings

        try:
            settings = ExtractionSettings()
            settings.require_model_name()
            extractors = create_configured_extractors(settings)
            model_predictions = _live_predictions(
                dataset, extractors.candidate, extractors.relation
            )
            model_id = f"{settings.provider}:{settings.require_model_name()}"
        except (ValueError, Exception):
            model_predictions = None
            model_id = None
            # Mark all extraction cases as skipped
            pass
    elif mode == "live-embedding":
        from memory_mcp.extraction.embedding import (
            QwenEmbeddingProvider,
        )
        from memory_mcp.extraction.settings import EmbeddingSettings

        try:
            emb_settings = EmbeddingSettings()
            provider = QwenEmbeddingProvider(emb_settings)
            recall_predictions = _live_embedding_predictions(dataset, provider)
            model_id = f"embedding:{emb_settings.model_name}"
        except Exception:
            recall_predictions = None
            model_id = None

    report = run_evaluation(
        dataset,
        mode=mode,
        model_predictions=model_predictions,
        recall_predictions=recall_predictions,
    )

    baseline = _load_baseline(mode)
    baseline_comparison = _compare_with_baseline(report, baseline)

    return {
        "run": {
            "mode": mode,
            "model_id": model_id,
            "git_commit": _git_commit(),
            "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "dataset_version": dataset.version,
            "started_at": started_at.isoformat(),
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "suite_filter": suite,
            "tag_filter": tag,
            "case_id_filter": case_id,
            "case_count": len(dataset.cases),
        },
        **report.as_dict(),
        "baseline_comparison": baseline_comparison,
    }


def _live_predictions(dataset, candidate_extractor, relation_extractor):
    """调用真实模型预测候选与关系。"""

    from datetime import UTC, datetime
    from uuid import NAMESPACE_URL, uuid5

    from memory_mcp.core import (
        MessageRole,
        PrincipalContext,
        ProfileRegistry,
        TurnEnvelope,
        TurnMessage,
    )
    from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
    from memory_mcp.core.application.automatic_relations import (
        AutomaticRelationPlanner,
    )
    from memory_mcp.core.composition import create_memory_service
    from memory_mcp.profiles import GeneralWorkProfile, InvestmentResearchProfile

    _now = datetime(2026, 8, 1, 10, tzinfo=UTC)
    _owner = PrincipalContext("evaluation-owner")
    _profiles = {
        "general-work": GeneralWorkProfile(),
        "investment-research": InvestmentResearchProfile(),
    }

    predictions: dict[str, frozenset[str]] = {}
    for case in dataset.cases:
        if isinstance(case, CandidateCase):
            profile = _profiles[case.profile_id]
            repository = InMemoryMemoryRepository()
            service = create_memory_service(
                repository,
                [profile],
                candidate_extractor=candidate_extractor,
            )
            service.capture_turn(
                _owner,
                TurnEnvelope(
                    profile_id=case.profile_id,
                    conversation_id=f"eval-{case.id}",
                    source_turn_id="turn-1",
                    content=case.content,
                    observed_at=_now,
                    messages=(
                        TurnMessage(
                            role=MessageRole(case.source_role),
                            content=case.content,
                            message_id="message-1",
                        ),
                    ),
                ),
            )
            predictions[case.id] = frozenset(
                record.item.memory_type
                for record in service.list_memories(_owner)
            )
        elif isinstance(case, RelationCase):
            profile = _profiles[case.profile_id]
            repository = InMemoryMemoryRepository()
            registry = ProfileRegistry()
            registry.register(profile)
            records = tuple(
                _endpoint_record(case, ep) for ep in case.endpoints
            )
            labels_by_id = {
                ep.memory_id: ep.label for ep in case.endpoints
            }
            planner = AutomaticRelationPlanner(
                repository,
                registry,
                relation_extractor,
                id_factory=lambda _cid=case.id: uuid5(
                    NAMESPACE_URL, f"relation:{_cid}"
                ),
                clock=lambda: _now,
            )
            plan = planner.plan(
                _owner,
                profile=profile,
                capture_id=uuid5(
                    NAMESPACE_URL, f"capture:{case.id}"
                ),
                conversation_id=f"eval-{case.id}",
                source_turn_id="turn-1",
                redacted_source=case.content,
                observed_at=_now,
                same_capture_memories=records,
                subject_hint=None,
                trusted_user_sources=(
                    (case.content,) if case.source_role == "user" else ()
                ),
            )
            predictions[case.id] = frozenset(
                "|".join(
                    (
                        rel.relation_type,
                        labels_by_id[rel.source_memory_id],
                        labels_by_id[rel.target_memory_id],
                    )
                )
                for rel in plan.relations
            )
    return predictions


def _endpoint_record(case, endpoint):
    """构造关系评测的记忆端点。"""

    from datetime import UTC, datetime
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
        SensitivityLevel,
        VerificationStatus,
    )

    _now = datetime(2026, 8, 1, 10, tzinfo=UTC)
    _owner = PrincipalContext("evaluation-owner")
    evidence_id = uuid5(NAMESPACE_URL, f"evidence:{case.id}:{endpoint.label}")
    return MemoryRecord(
        item=MemoryItem(
            memory_id=endpoint.memory_id,
            owner_id=_owner.owner_id,
            profile_id=case.profile_id,
            subject=endpoint.subject,
            memory_type=endpoint.memory_type,
            created_at=_now,
        ),
        current_revision=MemoryRevision(
            revision_id=endpoint.revision_id,
            memory_id=endpoint.memory_id,
            owner_id=_owner.owner_id,
            revision_number=1,
            content=endpoint.content,
            assertion_kind=AssertionKind.USER_VIEW,
            lifecycle_status=LifecycleStatus.ACTIVE,
            business_progress=None,
            save_rationale="evaluation fixture",
            observed_at=_now,
            created_at=_now,
            extraction_confidence=1.0,
            verification_status=VerificationStatus.USER_ASSERTED,
            sensitivity_level=SensitivityLevel.INTERNAL,
            valid_from=_now,
            valid_until=None,
        ),
        evidence=(
            Evidence(
                evidence_id=evidence_id,
                memory_id=endpoint.memory_id,
                revision_id=endpoint.revision_id,
                owner_id=_owner.owner_id,
                conversation_id=f"eval-{case.id}",
                source_turn_id="fixture",
                source_expression=endpoint.content,
                observed_at=_now,
                created_at=_now,
                source_role=MessageRole.USER,
            ),
        ),
    )


def _live_embedding_predictions(dataset, provider):
    """调用真实 EmbeddingProvider 生成召回预测。"""

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
    from memory_mcp.profiles import GeneralWorkProfile, InvestmentResearchProfile

    _owner = PrincipalContext("evaluation-owner")
    _now = datetime(2026, 8, 1, 10, tzinfo=UTC)
    _profiles = {
        "general-work": GeneralWorkProfile,
        "investment-research": InvestmentResearchProfile,
    }

    predictions: dict[str, tuple[str, ...]] = {}
    for case in dataset.cases:
        from evals.schema import RecallCase

        if not isinstance(case, RecallCase):
            continue
        profile_cls = _profiles.get(case.profile_id, InvestmentResearchProfile)
        profile = profile_cls()
        repository = InMemoryMemoryRepository()
        service = create_memory_service(
            repository,
            [profile],
            recall_candidate_limit=case.candidate_limit,
            embedding_provider=provider,
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
        predictions[case.id] = tuple(
            labels_by_id.get(item.memory_id, f"unknown:{item.memory_id}")
            for item in result.items
        )
    return predictions


def _render_markdown(payload: dict[str, Any]) -> str:
    """渲染可阅读的 Markdown 汇总。"""

    lines: list[str] = [
        f"# Evaluation Report ({payload['run']['mode']})",
        "",
        f"- **Dataset**: {payload['run']['dataset_version']} ({payload['run']['case_count']} cases)",
        f"- **Git**: {payload['run'].get('git_commit', 'N/A')}",
        f"- **Duration**: {payload['run']['duration_ms']}ms",
        f"- **Thresholds Met**: {'✅' if payload['thresholds_met'] else '❌'}",
        "",
        "## Metrics",
        "",
    ]
    for key in (
        "candidate",
        "relation",
        "recall_at_k",
        "precision_at_k",
        "mrr",
        "safety_pass_rate",
        "isolation_pass_rate",
        "lifecycle_pass_rate",
    ):
        val = payload.get(key)
        if isinstance(val, dict):
            lines.append(f"- **{key}**: P={val.get('precision', 'N/A')}, R={val.get('recall', 'N/A')}, F1={val.get('f1', 'N/A')}")
        elif val is not None:
            lines.append(f"- **{key}**: {val}")
    if payload.get("failed_case_ids"):
        lines.append("")
        lines.append("## Failed Cases")
        lines.append("")
        for cid in payload["failed_case_ids"]:
            lines.append(f"- {cid}")
    bc = payload.get("baseline_comparison", {})
    if bc.get("has_baseline"):
        lines.append("")
        lines.append("## Baseline Comparison")
        lines.append("")
        if bc.get("regression"):
            lines.append(f"⚠️ **Regression detected**: {', '.join(bc.get('regressed_metrics', []))}")
        else:
            lines.append("✅ No regression")
        for metric, change in bc.get("changes", {}).items():
            lines.append(f"- {metric}: {change['baseline']} → {change['current']} (Δ {change['delta']:+.6f})")
    skipped = payload.get("skipped_reasons", {})
    if skipped:
        lines.append("")
        lines.append("## Skipped Cases")
        lines.append("")
        for cid, reason in sorted(skipped.items()):
            lines.append(f"- {cid}: {reason}")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Memory MCP quality")
    parser.add_argument(
        "--mode",
        choices=["deterministic", "live-extraction", "live-embedding"],
        default="deterministic",
        help="evaluation mode (default: deterministic)",
    )
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument("--suite", type=str, help="filter by suite")
    parser.add_argument("--tag", type=str, help="filter by tag")
    parser.add_argument("--case-id", type=str, help="filter by case id")
    parser.add_argument("--output", type=Path, help="write JSON report")
    parser.add_argument(
        "--markdown",
        type=Path,
        help="write Markdown report",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="update the baseline for the given mode (use with care)",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.output is not None and not args.output.parent.is_dir():
        raise ValueError("output parent directory must already exist")
    if args.markdown is not None and not args.markdown.parent.is_dir():
        raise ValueError("markdown parent directory must already exist")

    dataset = _filter_dataset(
        load_dataset(args.dataset),
        suite=args.suite,
        tag=args.tag,
        case_id=args.case_id,
    )
    payload = _run_payload(
        dataset,
        dataset_path=args.dataset,
        mode=args.mode,
        suite=args.suite,
        tag=args.tag,
        case_id=args.case_id,
    )

    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    if args.markdown is not None:
        args.markdown.write_text(_render_markdown(payload), encoding="utf-8")

    if args.update_baseline:
        _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        baseline_path = _BASELINE_DIR / f"{args.mode}.json"
        baseline_path.write_text(f"{rendered}\n", encoding="utf-8")
        print(f"baseline updated: {baseline_path}")

    print(rendered[:5000])  # truncate for stdout
    if len(rendered) > 5000:
        print(f"... (full report: {len(rendered)} chars)")

    # Deterministic regression: non-zero exit if thresholds not met
    if args.mode == "deterministic" and not payload["thresholds_met"]:
        return 1
    bc = payload.get("baseline_comparison", {})
    if bc.get("regression"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
