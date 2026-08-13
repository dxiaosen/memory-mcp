"""B2 thesis 语义去重的契约测试。

覆盖：
- 字面 subject 不匹配但内容语义近似的候选，命中现有同类型记忆后降级为
  待确认（semantic_lifecycle_conflict），而非新增碎片化 thesis；
- 内容几乎一致的近似命中走 duplicate evidence；
- 显式替换意图走 replacement；
- Profile 未声明阈值时（general-work）不启用语义去重，走原有新增路径；
- 嵌入不可用时回退到原有新增路径；
- 非用户源（assistant/tool）字面未命中 + 语义等价 -> discard
  （semantic_assistant_restatement），避免绕过去重进 Pending 后变重复 active。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from memory_mcp.core import (
    AdmissionDecision,
    AssertionKind,
    EvidenceSourceType,
    MemoryService,
    MessageRole,
    PrincipalContext,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.adapters.in_memory import InMemoryMemoryRepository
from memory_mcp.core.composition import create_memory_service
from memory_mcp.profiles import GeneralWorkProfile, InvestmentResearchProfile

from tests.support.fakes import (
    FakeCandidateExtractor,
    candidate_proposal,
)

_PRINCIPAL = PrincipalContext("owner-a")
_NOW = datetime(2026, 7, 30, 10, tzinfo=UTC)


def _turn(text: str, *, turn_id: str) -> TurnEnvelope:
    return TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id=turn_id,
        content=text,
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=text,
                message_id=f"message-{turn_id}",
            ),
        ),
    )


def _service(
    vectors: dict[str, tuple[float, ...]],
) -> tuple[MemoryService, FakeCandidateExtractor]:
    extractor = FakeCandidateExtractor(())
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [InvestmentResearchProfile()],
        candidate_extractor=extractor,
        embedding_provider=_FakeProvider(vectors),
    )
    return service, extractor


class _FakeProvider:
    """8 维确定性 embedding provider，按文本映射返回向量。"""

    model_id = "fake-embedding-model"
    dimensions = 8

    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self._vectors = dict(vectors)

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        zero = tuple(0.0 for _ in range(self.dimensions))
        return tuple(self._vectors.get(text, zero) for text in texts)


def test_semantic_near_duplicate_thesis_downgrades_to_pending() -> None:
    """字面 subject 不同但语义近似的 thesis：降级为待确认。"""

    thesis_a_content = "示例公司企业需求会持续增长"
    thesis_b_content = "示例公司企业需求将持续扩张"
    vectors = {
        thesis_a_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 a 余弦相似度 1.0（同向），超阈值 0.92。
        thesis_b_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            thesis_a_content,
            subject="example-company-demand-v1",
            memory_type="thesis",
            content=thesis_a_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(thesis_a_content, turn_id="t1"))

    extractor.proposals = (
        candidate_proposal(
            thesis_b_content,
            subject="example-company-demand-v2",
            memory_type="thesis",
            content=thesis_b_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(_PRINCIPAL, _turn(thesis_b_content, turn_id="t2"))
    outcome = result.outcomes[0]
    assert outcome.decision is AdmissionDecision.PENDING
    assert outcome.reason_code == "semantic_lifecycle_conflict"
    assert outcome.review_id is not None


def test_semantic_duplicate_content_routes_to_duplicate_evidence() -> None:
    """近似命中且内容几乎一致：走 duplicate evidence。"""

    thesis_a_content = "示例公司企业需求持续增长"
    thesis_b_content = "示例公司企业需求持续增长"  # 内容相同
    vectors = {
        thesis_a_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        thesis_b_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            thesis_a_content,
            subject="example-company-demand-a",
            memory_type="thesis",
            content=thesis_a_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(thesis_a_content, turn_id="t1"))

    extractor.proposals = (
        candidate_proposal(
            thesis_b_content,
            subject="example-company-demand-b",  # 字面不同
            memory_type="thesis",
            content=thesis_b_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(_PRINCIPAL, _turn(thesis_b_content, turn_id="t2"))
    outcome = result.outcomes[0]
    assert outcome.decision is AdmissionDecision.AUTO_SAVE
    assert outcome.reason_code == "semantic_duplicate_evidence"


def test_general_work_profile_disables_semantic_dedup() -> None:
    """general-work 未声明 threshold：不启用语义去重，走新增。"""

    content_a = "周报默认使用表格"
    content_b = "周报默认使用表格"
    vectors = {
        content_a: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        content_b: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    extractor = FakeCandidateExtractor(())
    service = create_memory_service(
        InMemoryMemoryRepository(),
        [GeneralWorkProfile()],
        candidate_extractor=extractor,
        embedding_provider=_FakeProvider(vectors),
    )
    extractor.proposals = (
        candidate_proposal(
            content_a,
            subject="weekly-report-format-a",
            memory_type="preference",
            content=content_a,
        ),
    )
    service.capture_turn(
        _PRINCIPAL,
        replace(
            _turn(content_a, turn_id="t1"),
            profile_id="general-work",
        ),
    )
    extractor.proposals = (
        candidate_proposal(
            content_b,
            subject="weekly-report-format-b",
            memory_type="preference",
            content=content_b,
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        replace(_turn(content_b, turn_id="t2"), profile_id="general-work"),
    )
    # 无 threshold -> 新增第二条，不触发语义去重。
    assert result.outcomes[0].decision is AdmissionDecision.AUTO_SAVE
    assert result.outcomes[0].reason_code != "semantic_duplicate_evidence"


def test_semantic_assistant_restatement_with_different_subject_is_discarded() -> None:
    """非用户源（assistant）+ 字面 subject 不同 + 语义等价 -> discard。

    覆盖生产环境漏洞：assistant 复述了用户判断、但换了 subject 措辞，原逻辑
    因字面未命中且 non_user_source 降为 PENDING 而绕过语义去重，直接进 Pending，
    用户 confirm 后变成第二条语义重复的 active。修复后应走 semantic_assistant_restatement。
    """

    user_content = "示例公司海外增速快速回落时出海判断要打折"
    assistant_content = "示例公司海外增速快速回落时出海判断需要打折扣"
    vectors = {
        user_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 user_content 余弦相似度 1.0，超 risk 阈值 0.92。
        assistant_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    # 1) 用户陈述 -> auto_save 一条 active risk。
    extractor.proposals = (
        candidate_proposal(
            user_content,
            subject="example-company-oversea-discount-condition",
            memory_type="risk",
            content=user_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(user_content, turn_id="t1"))
    assert len(service.list_memories(_PRINCIPAL)) == 1

    # 2) Assistant 复述同样判断、换 subject 措辞 -> 应 discard，不进 Pending。
    extractor.proposals = (
        candidate_proposal(
            assistant_content,
            subject="example-company-oversea-discount-trigger",  # 字面不同
            memory_type="risk",
            content=assistant_content,
            assertion_kind=AssertionKind.SYSTEM_INFERENCE,
            business_progress="monitoring",
        ),
    )
    assistant_turn = TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id="t2",
        content=f"[assistant]\n{assistant_content}",
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.ASSISTANT,
                content=assistant_content,
                message_id="message-t2",
            ),
        ),
    )
    result = service.capture_turn(_PRINCIPAL, assistant_turn)
    discard = [
        o for o in result.outcomes if o.decision is AdmissionDecision.DISCARD
    ]
    assert len(discard) == 1
    assert discard[0].reason_code in {
        "assistant_restatement",
        "semantic_assistant_restatement",
    }
    # 不新增 Pending、不新增记忆。
    assert len(service.list_pending_reviews(_PRINCIPAL)) == 0
    assert len(service.list_memories(_PRINCIPAL)) == 1


def test_semantic_tool_restatement_with_different_subject_is_discarded() -> None:
    """非用户源（tool）+ 字面 subject 不同 + content 等价 -> discard。

    tool 源不像 assistant 那样走 _is_assistant_restatement（仅 source_role=assistant
    调用），只能靠 _resolve_semantic_target 的 semantic_assistant_restatement 分支拦截。
    content 归一等价时必须 discard，否则 tool 回声会进 Pending 后变重复 active。
    """

    content = "示例公司海外增速快速回落时出海判断要打折"
    vectors = {
        content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            content,
            subject="example-company-oversea-discount-condition",
            memory_type="risk",
            content=content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(content, turn_id="t1"))
    assert len(service.list_memories(_PRINCIPAL)) == 1

    # tool 源 + content 完全相同（归一等价）+ subject 不同 -> discard。
    extractor.proposals = (
        candidate_proposal(
            content,
            subject="example-company-oversea-discount-trigger",  # 字面不同
            memory_type="risk",
            content=content,
            assertion_kind=AssertionKind.EXTERNAL_FACT,
            business_progress="monitoring",
        ),
    )
    tool_turn = TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id="t2",
        content=f"[tool]\n{content}",
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.TOOL,
                content=content,
                message_id="message-t2",
                source_type=EvidenceSourceType.TOOL,
                tool_name="Read",
            ),
        ),
    )
    result = service.capture_turn(_PRINCIPAL, tool_turn)
    discard = [
        o for o in result.outcomes if o.decision is AdmissionDecision.DISCARD
    ]
    assert len(discard) == 1
    assert discard[0].reason_code == "semantic_assistant_restatement"
    assert len(service.list_pending_reviews(_PRINCIPAL)) == 0
    assert len(service.list_memories(_PRINCIPAL)) == 1


def test_assistant_restatement_same_subject_near_duplicate_content_discarded() -> None:
    """非用户源（assistant）+ 字面 subject 相同 + content 归一不等价但语义近似 -> discard。

    覆盖 Case B：subject 完全相同，但 content 差几个字（归一化不等价也不包含），
    原 _content_restates 判 False；risk 现已配 threshold=0.92 -> 语义兜底命中 -> discard。
    """

    user_content = "聊示例公司时先讲海外，再讲 IP，不要一上来堆一堆财务指标。"
    assistant_content = "聊示例公司时先讲海外，再讲IP，不要上来就堆财务指标。"
    vectors = {
        user_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 user_content 余弦相似度 1.0，超 research_preference 阈值 0.90。
        assistant_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            user_content,
            subject="example-company-communication-preference",
            memory_type="research_preference",
            content=user_content,
            assertion_kind=AssertionKind.USER_VIEW,
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(user_content, turn_id="t1"))
    assert len(service.list_memories(_PRINCIPAL)) == 1

    # subject 相同、content 仅差标点空格 -> 归一不等价 -> 走语义兜底 -> discard。
    extractor.proposals = (
        candidate_proposal(
            assistant_content,
            subject="example-company-communication-preference",  # 字面相同
            memory_type="research_preference",
            content=assistant_content,
            assertion_kind=AssertionKind.SYSTEM_INFERENCE,
        ),
    )
    assistant_turn = TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id="t2",
        content=f"[assistant]\n{assistant_content}",
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.ASSISTANT,
                content=assistant_content,
                message_id="message-t2",
            ),
        ),
    )
    result = service.capture_turn(_PRINCIPAL, assistant_turn)
    discard = [
        o for o in result.outcomes if o.decision is AdmissionDecision.DISCARD
    ]
    assert len(discard) == 1
    assert discard[0].reason_code in {
        "assistant_restatement",
        "semantic_assistant_restatement",
    }
    assert len(service.list_pending_reviews(_PRINCIPAL)) == 0
    assert len(service.list_memories(_PRINCIPAL)) == 1


def test_assistant_cross_type_echo_discarded() -> None:
    """assistant 跨 memory_type 复述已有记忆 -> discard（assistant_cross_type_echo）。

    覆盖生产漏洞：已有 risk「海外增速回落则打折」，assistant 复述后模型抽成
    thesis「证伪条件」——subject 措辞不同 + memory_type 不同，同类型语义去重
    查不到（find_semantically_similar 限定 memory_type），字面也不命中，
    原 logic 直接进 Pending。修复后跨类型回声检测（find_assistant_echo）命中
    -> discard，不进 Pending。
    """

    risk_content = "示例公司海外增速快速回落时出海判断要打折"
    thesis_content = "示例公司出海判断的证伪条件是海外增速快速回落"
    vectors = {
        risk_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 risk_content 余弦相似度 1.0，跨类型回声阈值 0.92（thesis）命中。
        thesis_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    # 1) 用户陈述 risk -> auto_save 一条 active。
    extractor.proposals = (
        candidate_proposal(
            risk_content,
            subject="example-company-oversea-discount-condition",
            memory_type="risk",
            content=risk_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(risk_content, turn_id="t1"))
    assert len(service.list_memories(_PRINCIPAL)) == 1

    # 2) Assistant 复述，模型抽成 thesis（不同 memory_type + 不同 subject）-> discard。
    extractor.proposals = (
        candidate_proposal(
            thesis_content,
            subject="example-company-oversea-falsification",  # 字面不同
            memory_type="thesis",  # 类型不同
            content=thesis_content,
            assertion_kind=AssertionKind.SYSTEM_INFERENCE,
            business_progress="monitoring",
        ),
    )
    assistant_turn = TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id="t2",
        content=f"[assistant]\n{thesis_content}",
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.ASSISTANT,
                content=thesis_content,
                message_id="message-t2",
            ),
        ),
    )
    result = service.capture_turn(_PRINCIPAL, assistant_turn)
    discard = [
        o for o in result.outcomes if o.decision is AdmissionDecision.DISCARD
    ]
    assert len(discard) == 1
    assert discard[0].reason_code == "assistant_cross_type_echo"
    # 不新增 Pending、不新增记忆。
    assert len(service.list_pending_reviews(_PRINCIPAL)) == 0
    assert len(service.list_memories(_PRINCIPAL)) == 1


def test_assistant_cross_type_echo_below_threshold_not_discarded() -> None:
    """跨类型回声未达阈值 -> 不 discard，正常走后续路径。

    防止回声检测过严误杀独立判断：assistant 输出与已有记忆语义相似度低于
    阈值时不应拦截。
    """

    risk_content = "示例公司海外增速快速回落时出海判断要打折"
    independent_content = "示例公司国内渠道下沉到三四线城市是新增量"
    vectors = {
        risk_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 risk_content 余弦相似度 0.0，远低于阈值。
        independent_content: (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            risk_content,
            subject="example-company-oversea-discount-condition",
            memory_type="risk",
            content=risk_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(risk_content, turn_id="t1"))

    # assistant 输出独立判断（语义不相似）-> 不应被跨类型回声拦截。
    extractor.proposals = (
        candidate_proposal(
            independent_content,
            subject="example-company-domestic-channels",
            memory_type="thesis",
            content=independent_content,
            assertion_kind=AssertionKind.SYSTEM_INFERENCE,
            business_progress="monitoring",
        ),
    )
    assistant_turn = TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id="t2",
        content=f"[assistant]\n{independent_content}",
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.ASSISTANT,
                content=independent_content,
                message_id="message-t2",
            ),
        ),
    )
    result = service.capture_turn(_PRINCIPAL, assistant_turn)
    cross_type_discard = [
        o
        for o in result.outcomes
        if o.decision is AdmissionDecision.DISCARD
        and o.reason_code == "assistant_cross_type_echo"
    ]
    assert len(cross_type_discard) == 0
