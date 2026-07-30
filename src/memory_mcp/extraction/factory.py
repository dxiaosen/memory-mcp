"""Composition helpers for configured candidate extraction."""

from typing import Literal, Protocol

from memory_mcp.core.adapters.structured_model import StructuredCandidateExtractor
from memory_mcp.core.ports import CandidateExtractor
from memory_mcp.extraction.backends import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    FixedCandidateBackend,
    LangChainCandidateBackend,
    SupportsStructuredOutput,
)
from memory_mcp.extraction.chat_models import create_chat_model
from memory_mcp.extraction.settings import ChatModelSettings


class CandidateExtractorConfiguration(Protocol):
    extractor_backend: Literal["fixed", "openai-compatible"]

    def fixed_candidates_payload(self) -> str: ...


def create_configured_candidate_extractor(
    settings: CandidateExtractorConfiguration,
    *,
    chat_model_settings: ChatModelSettings | None = None,
    chat_model: SupportsStructuredOutput | None = None,
) -> CandidateExtractor:
    """Build the selected extractor and fail during startup on bad configuration."""

    if settings.extractor_backend == "fixed":
        backend = FixedCandidateBackend.from_json(settings.fixed_candidates_payload())
        return StructuredCandidateExtractor(
            backend,
            model_id="fixed-candidate-catalog",
            prompt_version="fixed-exact-match-v1",
            schema_version=SCHEMA_VERSION,
        )

    resolved_chat_settings = chat_model_settings or ChatModelSettings()
    resolved_model = chat_model or create_chat_model(resolved_chat_settings)
    return StructuredCandidateExtractor(
        LangChainCandidateBackend(resolved_model),
        model_id=(
            f"{resolved_chat_settings.chat_model_provider}:"
            f"{resolved_chat_settings.chat_model_name}"
        ),
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
