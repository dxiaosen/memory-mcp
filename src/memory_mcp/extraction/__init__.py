"""模型辅助候选抽取的公开 API。"""

from memory_mcp.extraction.backends import (
    CandidateBatch,
    CandidateOutput,
    FixedCandidateBackend,
    LangChainCandidateBackend,
)
from memory_mcp.extraction.chat_models import create_chat_model
from memory_mcp.extraction.factory import create_configured_candidate_extractor
from memory_mcp.extraction.settings import (
    ExtractionProvider,
    ExtractionSettings,
)

__all__ = [
    "CandidateBatch",
    "CandidateOutput",
    "ExtractionProvider",
    "ExtractionSettings",
    "FixedCandidateBackend",
    "LangChainCandidateBackend",
    "create_chat_model",
    "create_configured_candidate_extractor",
]
