"""把任意结构化模型后端适配为候选与关系 Extractor。"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from memory_mcp.core.domain import (
    AssertionKind,
    CandidateDurability,
    CandidateProposal,
    ExpressionBasis,
    RelationProposal,
)
from memory_mcp.core.exceptions import InvalidModelOutputError
from memory_mcp.core.ports import (
    MAX_RELATION_PROPOSALS,
    ExtractionRequest,
    RelationExtractionRequest,
)

StructuredModelBackend = Callable[
    [ExtractionRequest],
    Sequence[Mapping[str, Any]],
]
StructuredRelationBackend = Callable[
    [RelationExtractionRequest],
    Sequence[Mapping[str, Any]],
]


class StructuredCandidateExtractor:
    """解析结构化字典输出，不依赖具体模型 SDK。"""

    def __init__(
        self,
        backend: StructuredModelBackend,
        *,
        model_id: str,
        prompt_version: str,
        schema_version: str = "candidate-v1",
    ) -> None:
        self._backend = backend
        self._model_id = _required_text(model_id, "model_id")
        self._prompt_version = _required_text(
            prompt_version,
            "prompt_version",
        )
        self._schema_version = _required_text(
            schema_version,
            "schema_version",
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def schema_version(self) -> str:
        return self._schema_version

    def extract(self, request: ExtractionRequest) -> tuple[CandidateProposal, ...]:
        """调用后端并逐条解析候选；非法输出转为 ``InvalidModelOutputError``。"""

        try:
            payload = self._backend(request)
            return tuple(_parse_candidate(item) for item in payload)
        except InvalidModelOutputError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise InvalidModelOutputError(
                "structured candidate output is invalid"
            ) from exc


class StructuredRelationExtractor:
    """解析结构化关系输出，不依赖具体模型 SDK。"""

    def __init__(
        self,
        backend: StructuredRelationBackend,
        *,
        model_id: str,
        prompt_version: str,
        schema_version: str = "relation-v1",
    ) -> None:
        self._backend = backend
        self._model_id = _required_text(model_id, "model_id")
        self._prompt_version = _required_text(prompt_version, "prompt_version")
        self._schema_version = _required_text(schema_version, "schema_version")

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def schema_version(self) -> str:
        return self._schema_version

    def extract(
        self,
        request: RelationExtractionRequest,
    ) -> tuple[RelationProposal, ...]:
        """调用后端解析关系；超限或非法输出转为 ``InvalidModelOutputError``。"""

        try:
            payload = self._backend(request)
            if len(payload) > MAX_RELATION_PROPOSALS:
                raise InvalidModelOutputError("relation proposal limit exceeded")
            return tuple(_parse_relation(item) for item in payload)
        except InvalidModelOutputError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise InvalidModelOutputError(
                "structured relation output is invalid"
            ) from exc


def _parse_candidate(payload: Mapping[str, Any]) -> CandidateProposal:
    if not isinstance(payload, Mapping):
        raise InvalidModelOutputError("candidate must be an object")
    return CandidateProposal(
        subject=_required_text(payload.get("subject"), "subject"),
        memory_type=_required_text(
            payload.get("memory_type"),
            "memory_type",
        ),
        content=_required_text(payload.get("content"), "content"),
        assertion_kind=_enum_value(
            AssertionKind,
            payload.get("assertion_kind"),
            "assertion_kind",
        ),
        source_expression=_required_text(
            payload.get("source_expression"),
            "source_expression",
        ),
        save_rationale=_required_text(
            payload.get("save_rationale"),
            "save_rationale",
        ),
        confidence=_confidence(payload.get("confidence")),
        durability=_enum_value(
            CandidateDurability,
            payload.get("durability"),
            "durability",
        ),
        expression_basis=_enum_value(
            ExpressionBasis,
            payload.get("expression_basis"),
            "expression_basis",
        ),
        business_progress=_optional_text(
            payload.get("business_progress"),
            "business_progress",
        ),
        original_time_expression=_optional_text(
            payload.get("original_time_expression"),
            "original_time_expression",
        ),
        normalized_time=_optional_datetime(
            payload.get("normalized_time"),
            "normalized_time",
        ),
        proposed_owner_id=_optional_text(
            payload.get("owner_id"),
            "owner_id",
        ),
        proposed_conversation_id=_optional_text(
            payload.get("conversation_id"),
            "conversation_id",
        ),
        proposed_source_turn_id=_optional_text(
            payload.get("source_turn_id"),
            "source_turn_id",
        ),
        proposed_observed_at=_optional_datetime(
            payload.get("observed_at"),
            "observed_at",
        ),
    )


def _parse_relation(payload: Mapping[str, Any]) -> RelationProposal:
    if not isinstance(payload, Mapping):
        raise InvalidModelOutputError("relation must be an object")
    expected_fields = {
        "source_memory_id",
        "target_memory_id",
        "relation_type",
        "source_expression",
        "confidence",
        "expression_basis",
    }
    if set(payload) != expected_fields:
        raise InvalidModelOutputError("relation fields do not match the schema")
    return RelationProposal(
        source_memory_id=_uuid_value(
            payload.get("source_memory_id"),
            "source_memory_id",
        ),
        target_memory_id=_uuid_value(
            payload.get("target_memory_id"),
            "target_memory_id",
        ),
        relation_type=_required_text(
            payload.get("relation_type"),
            "relation_type",
        ),
        source_expression=_required_text(
            payload.get("source_expression"),
            "source_expression",
        ),
        confidence=_confidence(payload.get("confidence")),
        expression_basis=_enum_value(
            ExpressionBasis,
            payload.get("expression_basis"),
            "expression_basis",
        ),
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidModelOutputError(f"{field_name} must be non-empty text")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidModelOutputError("confidence must be numeric")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise InvalidModelOutputError("confidence must be between 0 and 1")
    return confidence


def _uuid_value(value: object, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise InvalidModelOutputError(f"{field_name} must be a UUID") from exc
    raise InvalidModelOutputError(f"{field_name} must be a UUID")


def _optional_datetime(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidModelOutputError(
                f"{field_name} must be an ISO datetime"
            ) from exc
    else:
        raise InvalidModelOutputError(f"{field_name} must be a datetime or ISO text")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidModelOutputError(f"{field_name} must be timezone-aware")
    return parsed


def _enum_value[T](
    enum_type: type[T],
    value: object,
    field_name: str,
) -> T:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except (TypeError, ValueError) as exc:
        raise InvalidModelOutputError(f"{field_name} is invalid") from exc
