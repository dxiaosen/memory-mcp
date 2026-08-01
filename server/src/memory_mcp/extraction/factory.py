"""真实模型候选与关系抽取的组合辅助函数。"""

from dataclasses import dataclass

from memory_mcp.core.adapters.structured_model import (
    StructuredCandidateExtractor,
    StructuredRelationExtractor,
)
from memory_mcp.core.ports import CandidateExtractor, RelationExtractor
from memory_mcp.extraction.backends import (
    PROMPT_VERSION,
    RELATION_PROMPT_VERSION,
    RELATION_SCHEMA_VERSION,
    SCHEMA_VERSION,
    LangChainCandidateBackend,
    LangChainRelationBackend,
    SupportsStructuredOutput,
)
from memory_mcp.extraction.chat_models import create_chat_model
from memory_mcp.extraction.settings import ExtractionSettings


@dataclass(frozen=True, slots=True)
class ConfiguredExtractors:
    """共享一个 ChatModel 的两个严格抽取器。"""

    candidate: CandidateExtractor
    relation: RelationExtractor


def create_configured_candidate_extractor(
    settings: ExtractionSettings,
    *,
    chat_model: SupportsStructuredOutput | None = None,
) -> CandidateExtractor:
    """构建真实模型抽取器，配置错误时在启动阶段失败。"""

    resolved_model = chat_model or create_chat_model(settings)
    return _candidate_extractor(settings, resolved_model)


def create_configured_extractors(
    settings: ExtractionSettings,
    *,
    chat_model: SupportsStructuredOutput | None = None,
) -> ConfiguredExtractors:
    """一次创建模型，并为候选和关系分别绑定严格 schema。"""

    resolved_model = chat_model or create_chat_model(settings)
    return ConfiguredExtractors(
        candidate=_candidate_extractor(settings, resolved_model),
        relation=StructuredRelationExtractor(
            LangChainRelationBackend(resolved_model),
            model_id=_model_id(settings),
            prompt_version=RELATION_PROMPT_VERSION,
            schema_version=RELATION_SCHEMA_VERSION,
        ),
    )


def _candidate_extractor(
    settings: ExtractionSettings,
    model: SupportsStructuredOutput,
) -> StructuredCandidateExtractor:
    return StructuredCandidateExtractor(
        LangChainCandidateBackend(model),
        model_id=_model_id(settings),
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )


def _model_id(settings: ExtractionSettings) -> str:
    return f"{settings.provider}:{settings.require_model_name()}"
