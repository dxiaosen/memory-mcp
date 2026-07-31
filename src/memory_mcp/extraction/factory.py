"""真实模型候选抽取的组合辅助函数。"""

from memory_mcp.core.adapters.structured_model import StructuredCandidateExtractor
from memory_mcp.core.ports import CandidateExtractor
from memory_mcp.extraction.backends import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    LangChainCandidateBackend,
    SupportsStructuredOutput,
)
from memory_mcp.extraction.chat_models import create_chat_model
from memory_mcp.extraction.settings import ExtractionSettings


def create_configured_candidate_extractor(
    settings: ExtractionSettings,
    *,
    chat_model: SupportsStructuredOutput | None = None,
) -> CandidateExtractor:
    """构建真实模型抽取器，配置错误时在启动阶段失败。"""

    resolved_model = chat_model or create_chat_model(settings)
    return StructuredCandidateExtractor(
        LangChainCandidateBackend(resolved_model),
        model_id=(f"{settings.provider}:{settings.require_model_name()}"),
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
