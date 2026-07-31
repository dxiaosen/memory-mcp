from datetime import UTC, datetime

import pytest
from memory_mcp.core import (
    AssertionKind,
    CandidateDurability,
    ExpressionBasis,
    ExtractionRequest,
    InvalidModelOutputError,
)
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.adapters.structured_model import StructuredCandidateExtractor


def test_structured_model_adapter_parses_candidates_and_exposes_versions() -> None:
    extractor = StructuredCandidateExtractor(
        lambda request: [
            {
                "subject": "weekly-report",
                "memory_type": "preference",
                "content": "项目周报默认使用表格",
                "assertion_kind": "user_view",
                "source_expression": request.content,
                "save_rationale": "明确且持续有效",
                "confidence": 0.96,
                "durability": "durable",
                "expression_basis": "explicit",
                "owner_id": "untrusted-owner",
                "conversation_id": "untrusted-conversation",
                "source_turn_id": "untrusted-turn",
                "observed_at": "2025-01-01T00:00:00Z",
            }
        ],
        model_id="offline-model",
        prompt_version="prompt-v7",
        schema_version="candidate-v2",
    )

    proposals = extractor.extract(_request())

    assert extractor.model_id == "offline-model"
    assert extractor.prompt_version == "prompt-v7"
    assert extractor.schema_version == "candidate-v2"
    assert len(proposals) == 1
    assert proposals[0].assertion_kind is AssertionKind.USER_VIEW
    assert proposals[0].durability is CandidateDurability.DURABLE
    assert proposals[0].expression_basis is ExpressionBasis.EXPLICIT
    assert proposals[0].proposed_owner_id == "untrusted-owner"
    assert proposals[0].proposed_observed_at == datetime(
        2025,
        1,
        1,
        tzinfo=UTC,
    )


def test_structured_model_adapter_rejects_invalid_payload() -> None:
    extractor = StructuredCandidateExtractor(
        lambda request: [{"subject": "missing-other-fields"}],
        model_id="offline-model",
        prompt_version="prompt-v1",
    )

    with pytest.raises(InvalidModelOutputError):
        extractor.extract(_request())


@pytest.mark.parametrize(
    ("text", "category", "secret"),
    [
        ("密码是 abc123", "credential", "abc123"),
        ("账号: account-9988", "account_secret", "account-9988"),
        ("我持有1000股", "real_holding", "1000"),
        ("请买入100股示例股票", "transaction_instruction", "100股"),
    ],
)
def test_sensitive_guard_redacts_configured_prohibited_content(
    text: str,
    category: str,
    secret: str,
) -> None:
    result = RegexSensitiveContentGuard().inspect(text)

    assert category in result.categories
    assert secret not in result.redacted_text
    assert f"[REDACTED:{category}]" in result.redacted_text


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        profile_id="project-work",
        conversation_id="conversation-1",
        source_turn_id="turn-1",
        content="以后项目周报默认用表格",
        observed_at=datetime(2026, 7, 29, tzinfo=UTC),
        allowed_memory_types=frozenset({"preference"}),
        capture_guidance="Capture durable preference.",
        profile_version="profile-v1",
    )
