from datetime import UTC, datetime
from uuid import UUID

import pytest
from memory_mcp.core import (
    AssertionKind,
    CandidateDurability,
    ExpressionBasis,
    ExtractionRequest,
    InvalidModelOutputError,
    MemoryRelationPolicy,
    RelationEndpoint,
    RelationExtractionRequest,
)
from memory_mcp.core.adapters.sensitive import RegexSensitiveContentGuard
from memory_mcp.core.adapters.structured_model import (
    StructuredCandidateExtractor,
    StructuredRelationExtractor,
)


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


def test_structured_model_adapter_trims_to_soft_limit_by_confidence() -> None:
    """超过软上限 12 的候选按 confidence 降序裁剪，避免淹没准入管线。

    recommend.md §3：模型偶尔返回过多候选时，软裁剪（非整轮失败）+ 日志
    可观测。硬上限 MAX_CANDIDATES=20 仍由 schema 强制，这里验证 12< N ≤20
    的裁剪路径。
    """

    def backend(request: ExtractionRequest):
        return [
            {
                "subject": f"cand-{i}",
                "memory_type": "preference",
                "content": f"候选 {i}",
                "assertion_kind": "user_view",
                "source_expression": request.content,
                "save_rationale": "测试",
                "confidence": round(0.5 + i * 0.01, 2),
                "durability": "durable",
                "expression_basis": "explicit",
            }
            for i in range(15)
        ]

    extractor = StructuredCandidateExtractor(
        backend,
        model_id="offline-model",
        prompt_version="prompt-v1",
    )

    proposals = extractor.extract(_request())

    assert len(proposals) == 12
    # 按 confidence 降序取前 12：i=14..3（confidence 0.64..0.53）
    confidences = [p.confidence for p in proposals]
    assert confidences == sorted(confidences, reverse=True)
    assert max(confidences) == 0.64
    assert min(confidences) == 0.53



def test_structured_relation_adapter_parses_only_exact_schema() -> None:
    source_id = UUID("11111111-1111-1111-1111-111111111111")
    target_id = UUID("22222222-2222-2222-2222-222222222222")
    extractor = StructuredRelationExtractor(
        lambda request: [
            {
                "source_memory_id": str(source_id),
                "target_memory_id": str(target_id),
                "relation_type": "supports",
                "source_expression": request.content,
                "confidence": 0.96,
                "expression_basis": "explicit",
            }
        ],
        model_id="offline-relation-model",
        prompt_version="relation-prompt-v1",
    )

    proposals = extractor.extract(_relation_request(source_id, target_id))

    assert proposals[0].source_memory_id == source_id
    assert proposals[0].target_memory_id == target_id
    assert proposals[0].expression_basis is ExpressionBasis.EXPLICIT


@pytest.mark.parametrize(
    "mutation",
    (
        {"owner_id": "forged-owner"},
        {"source_memory_id": "not-a-uuid"},
    ),
)
def test_structured_relation_adapter_rejects_extra_identity_and_invalid_id(
    mutation: dict[str, str],
) -> None:
    source_id = UUID("11111111-1111-1111-1111-111111111111")
    target_id = UUID("22222222-2222-2222-2222-222222222222")
    payload = {
        "source_memory_id": str(source_id),
        "target_memory_id": str(target_id),
        "relation_type": "supports",
        "source_expression": "证据明确支持论点",
        "confidence": 0.96,
        "expression_basis": "explicit",
        **mutation,
    }
    extractor = StructuredRelationExtractor(
        lambda request: [payload],
        model_id="offline-relation-model",
        prompt_version="relation-prompt-v1",
    )

    with pytest.raises(InvalidModelOutputError):
        extractor.extract(_relation_request(source_id, target_id))


def test_structured_relation_adapter_rejects_more_than_twenty_proposals() -> None:
    source_id = UUID("11111111-1111-1111-1111-111111111111")
    target_id = UUID("22222222-2222-2222-2222-222222222222")
    payload = {
        "source_memory_id": str(source_id),
        "target_memory_id": str(target_id),
        "relation_type": "supports",
        "source_expression": "证据明确支持论点",
        "confidence": 0.96,
        "expression_basis": "explicit",
    }
    extractor = StructuredRelationExtractor(
        lambda request: [payload] * 21,
        model_id="offline-relation-model",
        prompt_version="relation-prompt-v1",
    )

    with pytest.raises(InvalidModelOutputError, match="limit"):
        extractor.extract(_relation_request(source_id, target_id))


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


def _relation_request(
    source_id: UUID,
    target_id: UUID,
) -> RelationExtractionRequest:
    return RelationExtractionRequest(
        profile_id="investment-research",
        content="证据明确支持论点",
        observed_at=datetime(2026, 7, 29, tzinfo=UTC),
        profile_version="v1",
        relation_policies={
            "supports": MemoryRelationPolicy(
                source_memory_types=frozenset({"evidence_claim"}),
                target_memory_types=frozenset({"thesis"}),
                description="Evidence supports a thesis.",
            )
        },
        endpoints=(
            RelationEndpoint(
                source_id,
                "evidence_claim",
                "company-evidence",
                "收入增长",
            ),
            RelationEndpoint(
                target_id,
                "thesis",
                "company-thesis",
                "增长趋势延续",
            ),
        ),
    )


def test_sensitive_guard_from_config_uses_default_when_unconfigured() -> None:
    """未配置规则时回退默认规则集。"""

    guard = RegexSensitiveContentGuard.from_config(None)
    result = guard.inspect("密码是 abc123")
    assert "credential" in result.categories


def test_sensitive_guard_from_config_injects_custom_rules() -> None:
    """配置注入的自定义规则覆盖默认规则集。"""

    configured = [
        {"category": "project_codename", "pattern": "阿波罗"},
    ]
    guard = RegexSensitiveContentGuard.from_config(configured)
    result = guard.inspect("项目代号阿波罗启动")
    assert "project_codename" in result.categories
    assert "[REDACTED:project_codename]" in result.redacted_text
    # 自定义规则集不包含默认 credential 规则。
    default_result = guard.inspect("密码是 abc123")
    assert "credential" not in default_result.categories


def test_sensitive_guard_from_config_drops_invalid_pattern() -> None:
    """非法正则模式在 settings 解析阶段被拒绝，不到达 guard。"""

    from memory_mcp.settings import MemoryServerSettings

    settings = MemoryServerSettings(
        auth_tokens='{"token": {"tenant_id": "t", "subject_id": "s"}}',
        sensitive_rules='[{"category": "bad", "pattern": "["}]',
    )
    with pytest.raises(ValueError, match="invalid"):
        settings.configured_sensitive_rules()


def test_sensitive_guard_from_config_rejects_non_array() -> None:
    """非 JSON 数组的敏感规则配置被拒绝。"""

    from memory_mcp.settings import MemoryServerSettings

    settings = MemoryServerSettings(
        auth_tokens='{"token": {"tenant_id": "t", "subject_id": "s"}}',
        sensitive_rules='{"category": "x", "pattern": "y"}',
    )
    with pytest.raises(ValueError, match="JSON array"):
        settings.configured_sensitive_rules()
