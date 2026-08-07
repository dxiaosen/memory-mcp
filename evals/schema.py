"""评估数据的严格、无身份字段合同。

支持三种评测模式：
- deterministic：确定性 extractor/embedding，不调真实模型，CI 门禁；
- live-extraction：真实 Chat Model 候选/关系抽取质量；
- live-embedding：真实 EmbeddingProvider 词法 vs 向量比较。
"""

import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

# ── 公共字段 ──


class StrictCase(BaseModel):
    """所有案例的公共字段。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    suite: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    mode: Literal["deterministic", "live-extraction", "live-embedding"]
    profile_id: Literal["general-work", "investment-research"]
    tags: tuple[str, ...] = Field(default=())
    category: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    rationale: str = Field(min_length=1)


# ── Candidate ──


class CandidateCase(StrictCase):
    task: Literal["candidate"]
    content: str = Field(min_length=1)
    source_role: Literal["user", "assistant"] = "user"
    expected: frozenset[str]


# ── Relation ──


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
    content: str = Field(min_length=1)
    source_role: Literal["user", "assistant"] = "user"
    endpoints: tuple[RelationEndpointCase, ...] = Field(min_length=2, max_length=40)
    expected: frozenset[str]

    @model_validator(mode="after")
    def validate_endpoints(self) -> RelationCase:
        labels = [e.label for e in self.endpoints]
        memory_ids = [e.memory_id for e in self.endpoints]
        revision_ids = [e.revision_id for e in self.endpoints]
        if len(labels) != len(set(labels)):
            raise ValueError("relation endpoint labels must be unique")
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("relation endpoint memory ids must be unique")
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("relation endpoint revision ids must be unique")
        return self


# ── Recall ──


class RecallCorpusItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    memory_type: str = Field(min_length=1)
    content: str = Field(min_length=1)
    observed_days_ago: int = Field(default=0, ge=0, le=10_000)
    embedding: tuple[float, ...] | None = None


class RecallCase(StrictCase):
    task: Literal["recall"]
    query: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=20)
    candidate_limit: int = Field(default=500, ge=1, le=10_000)
    token_budget: int = Field(default=8000, ge=64, le=80000)
    corpus: tuple[RecallCorpusItem, ...] = Field(min_length=1)
    expected: frozenset[str]

    @model_validator(mode="after")
    def validate_expected_labels(self) -> RecallCase:
        labels = [item.label for item in self.corpus]
        if len(labels) != len(set(labels)):
            raise ValueError("recall corpus labels must be unique")
        if not self.expected.issubset(labels):
            raise ValueError("recall expected labels must exist in corpus")
        return self


# ── Safety / Isolation ──


class SafetyCase(StrictCase):
    task: Literal["safety"]
    content: str = Field(min_length=1)
    expected_blocked: bool


class IsolationCase(StrictCase):
    """owner/team/profile 隔离 + MCP 参数注入防护。"""

    task: Literal["isolation"]
    content: str = Field(min_length=1)
    expected_blocked: bool


# ── Lifecycle ──


class LifecycleCase(StrictCase):
    """生命周期状态转换：duplicate/replacement/ambiguous/revoke/expire。"""

    task: Literal["lifecycle"]
    content: str = Field(min_length=1)
    second_content: str | None = None
    expected_transition: str = Field(min_length=1)


# ── 数据集 ──


EvaluationCase = Annotated[
    CandidateCase
    | RelationCase
    | RecallCase
    | SafetyCase
    | IsolationCase
    | LifecycleCase,
    Field(discriminator="task"),
]
_CASES = TypeAdapter(tuple[EvaluationCase, ...])


class EvaluationThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_precision: float = Field(default=0.0, ge=0.0, le=1.0)
    candidate_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    relation_precision: float = Field(default=0.0, ge=0.0, le=1.0)
    relation_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    recall_at_k: float = Field(default=0.0, ge=0.0, le=1.0)
    precision_at_k: float = Field(default=0.0, ge=0.0, le=1.0)
    mrr: float = Field(default=0.0, ge=0.0, le=1.0)
    safety_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    isolation_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    lifecycle_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    thresholds: EvaluationThresholds
    cases: tuple[EvaluationCase, ...]

    @model_validator(mode="after")
    def validate_case_ids(self) -> EvaluationDataset:
        identifiers = [case.id for case in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evaluation case ids must be unique")
        return self


def load_dataset(path: str | Path) -> EvaluationDataset:
    """先严格解析全部案例，再允许 runner 执行任何评估器。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evaluation dataset root must be an object")
    raw_cases = payload.get("cases")
    cases = _CASES.validate_python(raw_cases)
    return EvaluationDataset.model_validate({**payload, "cases": cases})
