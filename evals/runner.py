"""命令行评估入口；默认运行绝不会创建模型客户端。"""

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
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
    ProfileRegistry,
    SensitivityLevel,
    TurnEnvelope,
    TurnMessage,
    VerificationStatus,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.application.automatic_relations import AutomaticRelationPlanner
from memory_mcp.core.composition import create_memory_service
from memory_mcp.extraction.factory import create_configured_extractors
from memory_mcp.extraction.settings import ExtractionSettings
from memory_mcp.profiles import GeneralWorkProfile, InvestmentResearchProfile

from evals.metrics import EvaluationReport, Prediction, evaluate_dataset
from evals.schema import CandidateCase, EvaluationDataset, RelationCase, load_dataset

_NOW = datetime(2026, 8, 1, 10, tzinfo=UTC)
_OWNER = PrincipalContext("evaluation-owner")
_DEFAULT_DATASET = Path(__file__).with_name("cases.json")


def run_evaluation(
    dataset: EvaluationDataset,
    *,
    live_model: bool = False,
    live_predictor: Callable[[EvaluationDataset], dict[str, Prediction]] | None = None,
) -> EvaluationReport:
    """只有显式 live_model 才解析模型配置并创建外部客户端。"""

    predictions = None
    if live_model:
        predictions = (live_predictor or _live_predictions)(dataset)
    return evaluate_dataset(dataset, model_predictions=predictions)


def _live_predictions(dataset: EvaluationDataset) -> dict[str, Prediction]:
    extractors = create_configured_extractors(ExtractionSettings())
    predictions: dict[str, Prediction] = {}
    for case in dataset.cases:
        if isinstance(case, CandidateCase):
            predictions[case.id] = _candidate_prediction(
                case,
                extractors.candidate,
            )
        elif isinstance(case, RelationCase):
            predictions[case.id] = _relation_prediction(
                case,
                extractors.relation,
            )
    return predictions


def _candidate_prediction(case: CandidateCase, extractor) -> frozenset[str]:
    profile = _profiles()[case.profile_id]
    repository = InMemoryMemoryRepository()
    service = create_memory_service(
        repository,
        [profile],
        candidate_extractor=extractor,
    )
    role = MessageRole(case.source_role)
    service.capture_turn(
        _OWNER,
        TurnEnvelope(
            profile_id=case.profile_id,
            conversation_id=f"eval-{case.id}",
            source_turn_id="turn-1",
            content=case.content,
            observed_at=_NOW,
            messages=(
                TurnMessage(
                    role=role,
                    content=case.content,
                    message_id="message-1",
                ),
            ),
        ),
    )
    return frozenset(
        record.item.memory_type for record in service.list_memories(_OWNER)
    )


def _relation_prediction(case: RelationCase, extractor) -> frozenset[str]:
    profile = _profiles()[case.profile_id]
    repository = InMemoryMemoryRepository()
    registry = ProfileRegistry()
    registry.register(profile)
    records = tuple(_endpoint_record(case, endpoint) for endpoint in case.endpoints)
    labels_by_id = {endpoint.memory_id: endpoint.label for endpoint in case.endpoints}
    planner = AutomaticRelationPlanner(
        repository,
        registry,
        extractor,
        id_factory=lambda: uuid5(NAMESPACE_URL, f"relation:{case.id}"),
        clock=lambda: _NOW,
    )
    plan = planner.plan(
        _OWNER,
        profile=profile,
        capture_id=uuid5(NAMESPACE_URL, f"capture:{case.id}"),
        conversation_id=f"eval-{case.id}",
        source_turn_id="turn-1",
        redacted_source=case.content,
        observed_at=_NOW,
        same_capture_memories=records,
        subject_hint=None,
        trusted_user_sources=((case.content,) if case.source_role == "user" else ()),
    )
    return frozenset(
        "|".join(
            (
                relation.relation_type,
                labels_by_id[relation.source_memory_id],
                labels_by_id[relation.target_memory_id],
            )
        )
        for relation in plan.relations
    )


def _endpoint_record(case: RelationCase, endpoint) -> MemoryRecord:
    evidence_id = uuid5(NAMESPACE_URL, f"evidence:{case.id}:{endpoint.label}")
    item = MemoryItem(
        memory_id=endpoint.memory_id,
        owner_id=_OWNER.owner_id,
        profile_id=case.profile_id,
        subject=endpoint.subject,
        memory_type=endpoint.memory_type,
        created_at=_NOW,
    )
    revision = MemoryRevision(
        revision_id=endpoint.revision_id,
        memory_id=endpoint.memory_id,
        owner_id=_OWNER.owner_id,
        revision_number=1,
        content=endpoint.content,
        assertion_kind=AssertionKind.USER_VIEW,
        lifecycle_status=LifecycleStatus.ACTIVE,
        business_progress=None,
        save_rationale="evaluation fixture",
        observed_at=_NOW,
        created_at=_NOW,
        extraction_confidence=1.0,
        verification_status=VerificationStatus.USER_ASSERTED,
        sensitivity_level=SensitivityLevel.INTERNAL,
        valid_from=_NOW,
        valid_until=None,
        last_verified_at=None,
    )
    evidence = Evidence(
        evidence_id=evidence_id,
        memory_id=endpoint.memory_id,
        revision_id=endpoint.revision_id,
        owner_id=_OWNER.owner_id,
        conversation_id=f"eval-{case.id}",
        source_turn_id="fixture",
        source_expression=endpoint.content,
        observed_at=_NOW,
        created_at=_NOW,
        source_role=MessageRole.USER,
    )
    return MemoryRecord(item=item, current_revision=revision, evidence=(evidence,))


def _profiles():
    return {
        "general-work": GeneralWorkProfile(),
        "investment-research": InvestmentResearchProfile(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Memory MCP quality")
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument(
        "--live-model",
        action="store_true",
        help="explicitly call the configured external model",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = run_evaluation(
        load_dataset(args.dataset),
        live_model=args.live_model,
    )
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.thresholds_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
