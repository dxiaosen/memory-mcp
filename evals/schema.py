"""评估数据的严格、无身份字段合同。"""

import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class StrictCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    category: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")


class CandidateCase(StrictCase):
    task: Literal["candidate"]
    profile_id: Literal["general-work", "investment-research"]
    content: str = Field(min_length=1)
    source_role: Literal["user", "assistant"] = "user"
    expected: frozenset[str]


class RelationEndpointCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    memory_id: UUID
    revision_id: UUID
    memory_type: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    content: str = Field(min_length=1)


class RelationCase(StrictCase):
    task: Literal["relation"]
    profile_id: Literal["investment-research"] = "investment-research"
    content: str = Field(min_length=1)
    source_role: Literal["user", "assistant"] = "user"
    endpoints: tuple[RelationEndpointCase, ...] = Field(min_length=2, max_length=40)
    expected: frozenset[str]

    @model_validator(mode="after")
    def validate_endpoints(self) -> RelationCase:
        labels = [endpoint.label for endpoint in self.endpoints]
        memory_ids = [endpoint.memory_id for endpoint in self.endpoints]
        revision_ids = [endpoint.revision_id for endpoint in self.endpoints]
        if len(labels) != len(set(labels)):
            raise ValueError("relation endpoint labels must be unique")
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("relation endpoint memory ids must be unique")
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("relation endpoint revision ids must be unique")
        return self


class RecallCorpusItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    memory_type: str = Field(min_length=1)
    content: str = Field(min_length=1)


class RecallCase(StrictCase):
    task: Literal["recall"]
    query: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=20)
    corpus: tuple[RecallCorpusItem, ...] = Field(min_length=1)
    expected: frozenset[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_expected_labels(self) -> RecallCase:
        labels = [item.label for item in self.corpus]
        if len(labels) != len(set(labels)):
            raise ValueError("recall corpus labels must be unique")
        if not self.expected.issubset(labels):
            raise ValueError("recall expected labels must exist in corpus")
        return self


class SafetyCase(StrictCase):
    task: Literal["safety"]
    content: str = Field(min_length=1)
    expected_blocked: bool


EvaluationCase = Annotated[
    CandidateCase | RelationCase | RecallCase | SafetyCase,
    Field(discriminator="task"),
]
_CASES = TypeAdapter(tuple[EvaluationCase, ...])


class EvaluationThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_precision: float = Field(ge=0.0, le=1.0)
    candidate_recall: float = Field(ge=0.0, le=1.0)
    relation_precision: float = Field(ge=0.0, le=1.0)
    relation_recall: float = Field(ge=0.0, le=1.0)
    recall_at_k: float = Field(ge=0.0, le=1.0)
    safety_pass_rate: float = Field(ge=0.0, le=1.0)


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    benchmark_profile: Literal["investment-research"]
    thresholds: EvaluationThresholds
    cases: tuple[EvaluationCase, ...]

    @model_validator(mode="after")
    def validate_case_ids_and_coverage(self) -> EvaluationDataset:
        identifiers = [case.id for case in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evaluation case ids must be unique")
        tasks = {case.task for case in self.cases}
        required = {"candidate", "relation", "recall", "safety"}
        if tasks != required:
            raise ValueError("evaluation dataset must cover all supported tasks")
        candidate_types = frozenset(
            label
            for case in self.cases
            if isinstance(case, CandidateCase)
            for label in case.expected
        )
        missing_types = INVESTMENT_MEMORY_TYPES - candidate_types
        if missing_types:
            raise ValueError(
                "investment benchmark is missing candidate types: "
                + ", ".join(sorted(missing_types))
            )
        relation_types = frozenset(
            label.split("|", maxsplit=1)[0]
            for case in self.cases
            if isinstance(case, RelationCase)
            for label in case.expected
        )
        missing_relations = INVESTMENT_RELATION_TYPES - relation_types
        if missing_relations:
            raise ValueError(
                "investment benchmark is missing relation types: "
                + ", ".join(sorted(missing_relations))
            )
        return self


INVESTMENT_MEMORY_TYPES = frozenset(
    {
        "research_preference",
        "research_question",
        "thesis",
        "evidence_claim",
        "risk",
        "catalyst",
        "ongoing_research",
        "research_decision",
    }
)
INVESTMENT_RELATION_TYPES = frozenset(
    {
        "supports",
        "challenges",
        "threatens",
        "could_catalyze",
        "addresses",
        "resolves",
    }
)


def load_dataset(path: str | Path) -> EvaluationDataset:
    """先严格解析全部案例，再允许 runner 执行任何评估器。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evaluation dataset root must be an object")
    raw_cases = payload.get("cases")
    cases = _CASES.validate_python(raw_cases)
    return EvaluationDataset.model_validate({**payload, "cases": cases})
