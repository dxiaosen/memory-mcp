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
    ExpressionBasis,
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


def test_semantic_replacement_fallback_triggers_on_revised_judgment() -> None:
    """用户修订已有判断、model 抽的 subject 不同、语义近似 -> replacement fallback。

    覆盖生产漏洞：用户说"改一下/调整下"修订已有判断，但 model 抽的 subject 是
    新的（带"（修订）"后缀或换了措辞），字面 find_current 查不到。修复后：
    content 含修订意图词（"修订了"）-> _is_explicit_replacement 命中 ->
    semantic replacement fallback 用 0.60 宽松阈值查到语义近似的原判断 ->
    生成 replacement，原判断 superseded，而非新增第二条 active。
    """

    original_content = "示例公司出海是结构性的，海外占比持续提升"
    revised_content = "用户修订了示例公司出海判断标准：不能只看海外增速，还要看海外收入和多 IP 贡献"
    vectors = {
        original_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 original 余弦相似度 1.0，超 replacement fallback 阈值 0.60。
        revised_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    # 1) 用户原始判断 -> auto_save。
    extractor.proposals = (
        candidate_proposal(
            original_content,
            subject="example-company-oversea-thesis",
            memory_type="thesis",
            content=original_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(original_content, turn_id="t1"))
    original_memories = service.list_memories(_PRINCIPAL)
    assert len(original_memories) == 1
    original_id = original_memories[0].item.memory_id

    # 2) 用户修订，model 抽的 subject 不同、content 含"修订了" -> replacement。
    # source_expression 必须是 user 消息的逐字子串（provenance 校验）。
    revised_user_msg = (
        "我想改一下最开始那个判断。以后我判断示例公司出海不能只看海外增速，"
        "还要看多 IP 贡献"
    )
    extractor.proposals = (
        candidate_proposal(
            "以后我判断示例公司出海不能只看海外增速，还要看多 IP 贡献",
            subject="example-company-oversea-thesis-revised",  # 字面不同
            memory_type="thesis",
            content=revised_content,  # 含"修订了...判断标准"
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
            business_progress="monitoring",
        ),
    )
    revised_turn = TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id="t2",
        content=f"[user]\n{revised_user_msg}",
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=revised_user_msg,
                message_id="message-t2",
            ),
        ),
    )
    result = service.capture_turn(_PRINCIPAL, revised_turn)
    # 应生成 replacement，指向原判断 memory_id，reason_code=semantic_explicit_replacement。
    replacement_outcomes = [
        o
        for o in result.outcomes
        if o.decision is AdmissionDecision.AUTO_SAVE
        and o.reason_code == "semantic_explicit_replacement"
        and o.memory_id == original_id
    ]
    assert len(replacement_outcomes) == 1
    # 不应新增第二条 active（原判断被 superseded）。
    active = [m for m in service.list_memories(_PRINCIPAL)]
    assert len(active) == 1
    assert active[0].item.memory_id == original_id


def test_incremental_expansion_triggers_replacement() -> None:
    """用户"增加…关注"增量扩展已有判断 -> replacement，而非降 pending。

    覆盖生产漏洞（B 轮 6cb78316）：用户说"对于出海跑通的判断标准，增加对于
    增长回落点的关注"，这是对既有判断的增量修订——旧判断应被扩展后的新版本
    supersede。但"增加…关注"不命中原 `_EXPLICIT_REPLACEMENT` 词表 ->
    `_is_explicit_replacement`=False -> `_resolve_semantic_target` 走非替换分支
    -> `semantic_lifecycle_conflict` 降 pending，用户修订无法落地。
    """

    original_content = "出海跑通判断标准：重点看海外增速"
    # candidate content 含"增加…关注"——命中扩充后的 _EXPLICIT_REPLACEMENT。
    expanded_content = "出海跑通的判断标准增加对增长回落点的关注：重点看海外收入是否出现回落"
    vectors = {
        original_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 original 余弦相似度 1.0，超 replacement fallback 阈值 0.60。
        expanded_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            original_content,
            subject="oversea-passthrough-standard",
            memory_type="research_decision",
            content=original_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(original_content, turn_id="t1"))
    original_memories = service.list_memories(_PRINCIPAL)
    assert len(original_memories) == 1
    original_id = original_memories[0].item.memory_id

    # 用户增量扩展：source_expression 含"增加…关注"（命中扩充词表），
    # 且是 user 消息逐字子串（provenance 校验）。
    expansion_user_msg = "对于出海跑通的判断标准，增加对于增长回落点的关注"
    extractor.proposals = (
        candidate_proposal(
            expansion_user_msg,
            subject="oversea-passthrough-standard-expanded",
            memory_type="research_decision",
            content=expanded_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t2",
            content=f"[user]\n{expansion_user_msg}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=expansion_user_msg,
                    message_id="message-t2",
                ),
            ),
        ),
    )
    # 应生成 replacement（增量扩展命中 _EXPLICIT_REPLACEMENT），而非 pending。
    replacement_outcomes = [
        o
        for o in result.outcomes
        if o.decision is AdmissionDecision.AUTO_SAVE
        and o.reason_code == "semantic_explicit_replacement"
        and o.memory_id == original_id
    ]
    assert len(replacement_outcomes) == 1
    pending_outcomes = [
        o for o in result.outcomes if o.decision is AdmissionDecision.PENDING
    ]
    assert len(pending_outcomes) == 0
    # 旧判断被 superseded，不新增第二条 active。
    active = service.list_memories(_PRINCIPAL)
    assert len(active) == 1
    assert active[0].item.memory_id == original_id


def test_strong_match_overrides_ambiguous_margin() -> None:
    """replacement fallback：top1 强匹配（>=0.75）时即使 margin 不足也替换。

    覆盖生产漏洞（C 轮 f6e287b7）：用户复合修订被抽成一条涵盖多个子判断的
    超长 candidate，与两条相近记忆的 top1-top2 margin 不足 0.08 ->
    `ambiguous_semantic_replacement_target` 降 pending，修订无法落地。
    修复后：top1 相似度达强匹配阈值（0.75）时，top2 仅是同主题相关判断、
    不构成真歧义，允许替换 top1。
    """

    target_content = "出海跑通判断标准：重点看海外收入的回落点"
    sibling_content = "出海跑通判断标准：海外增速不是唯一指标"
    # candidate：含"调整"（命中 _EXPLICIT_REPLACEMENT），语义与 target 高度重叠。
    revised_content = "用户调整了出海跑通判断标准：海外部分重点看收入回落点而非增速"
    vectors = {
        # target 与 candidate 共用向量 -> 余弦相似度 1.0（>=0.75 强匹配）。
        target_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        revised_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # sibling 与 candidate 相似度 0.93：margin 0.07 < 0.08，但 top1=1.0 >= 0.75。
        # 构造：sibling = 0.93*e1 + 0.367*e2，归一化后与 e1 夹角余弦=0.93。
        sibling_content: (0.93, 0.367, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    # 先建两条同 type 的 active 记忆。
    extractor.proposals = (
        candidate_proposal(
            target_content,
            subject="oversea-standard-decline-point",
            memory_type="research_decision",
            content=target_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
        candidate_proposal(
            sibling_content,
            subject="oversea-standard-growth-speed",
            memory_type="research_decision",
            content=sibling_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t1",
            content=f"[user]\n{target_content}。{sibling_content}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=f"{target_content}。{sibling_content}",
                    message_id="message-t1",
                ),
            ),
        ),
    )
    assert len(service.list_memories(_PRINCIPAL)) == 2
    target_id = service.list_memories(_PRINCIPAL)[0].item.memory_id

    # 用户明确修订（含"调整"），语义与 target 高度重叠（sim=1.0），sibling 干扰（0.93）。
    revised_user_msg = "我调整下出海跑通的判断标准，海外部分重点看收入回落点"
    extractor.proposals = (
        candidate_proposal(
            revised_user_msg,
            subject="oversea-standard-revised",
            memory_type="research_decision",
            content=revised_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t2",
            content=f"[user]\n{revised_user_msg}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=revised_user_msg,
                    message_id="message-t2",
                ),
            ),
        ),
    )
    # top1=1.0 >= 0.75（强匹配）-> 即使 margin 0.07 < 0.08 也替换，不降 pending。
    pending_outcomes = [
        o for o in result.outcomes if o.decision is AdmissionDecision.PENDING
    ]
    assert len(pending_outcomes) == 0
    replacement_outcomes = [
        o
        for o in result.outcomes
        if o.decision is AdmissionDecision.AUTO_SAVE
        and o.reason_code == "semantic_explicit_replacement"
    ]
    assert len(replacement_outcomes) == 1
    assert replacement_outcomes[0].memory_id == target_id


def test_ambiguous_margin_below_strong_match_still_pending() -> None:
    """replacement fallback：top1 未达强匹配（<0.75）且 margin 不足 -> 仍降 pending。

    对照测试：确认 strong-match 豁免没有过度放松——top1 只刚过 fallback 阈值、
    与 top2 接近时仍保守判歧义降 pending，交用户确认。
    """

    target_content = "出海跑通判断标准 A 版本"
    sibling_content = "出海跑通判断标准 B 版本"
    revised_content = "用户调整了出海跑通的判断标准"
    vectors = {
        # target / sibling 与 candidate 相似度都 ~0.70（<0.75 强匹配，>=0.60 fallback）。
        target_content: (0.7, 0.714, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        sibling_content: (0.68, 0.733, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        revised_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            target_content,
            subject="oversea-standard-a",
            memory_type="research_decision",
            content=target_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
        candidate_proposal(
            sibling_content,
            subject="oversea-standard-b",
            memory_type="research_decision",
            content=sibling_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t1",
            content=f"[user]\n{target_content}。{sibling_content}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=f"{target_content}。{sibling_content}",
                    message_id="message-t1",
                ),
            ),
        ),
    )
    assert len(service.list_memories(_PRINCIPAL)) == 2

    revised_user_msg = "我调整下出海跑通的判断标准"
    extractor.proposals = (
        candidate_proposal(
            revised_user_msg,
            subject="oversea-standard-c",
            memory_type="research_decision",
            content=revised_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t2",
            content=f"[user]\n{revised_user_msg}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=revised_user_msg,
                    message_id="message-t2",
                ),
            ),
        ),
    )
    # top1~0.70 < 0.75、margin ~0.02 < 0.08 -> 仍判歧义降 pending。
    pending_outcomes = [
        o
        for o in result.outcomes
        if o.decision is AdmissionDecision.PENDING
        and o.reason_code == "ambiguous_semantic_replacement_target"
    ]
    assert len(pending_outcomes) == 1
    replacement_outcomes = [
        o
        for o in result.outcomes
        if o.decision is AdmissionDecision.AUTO_SAVE
        and o.reason_code == "semantic_explicit_replacement"
    ]
    assert len(replacement_outcomes) == 0


def test_cross_type_replacement_fallback_supersedes_old_type() -> None:
    """跨 type replacement fallback：旧 thesis 被修订成 risk，语义近似 -> 替代。

    覆盖生产漏洞：用户修订已有判断，但抽取模型把修正版抽成不同 memory_type
    （如旧 thesis「利润大增」被修订成 risk「利润有下行风险」）。同 type fallback
    查不到（type 不同）-> 跨 type fallback 命中语义近似的旧记忆 -> 生成跨 type
    replacement，旧记忆 superseded 且 memory_type 同步成 risk，不新增第二条 active。
    """

    original_content = "示例公司明年净利润大概率大幅增长"
    revised_content = "用户调整了对示例公司明年利润的判断，改为关注利润下行风险"
    vectors = {
        original_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 original 余弦相似度 1.0，超跨 type 强匹配阈值 0.75。
        revised_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    # 1) 用户原始判断 -> thesis auto_save。
    extractor.proposals = (
        candidate_proposal(
            original_content,
            subject="example-company-profit-thesis",
            memory_type="thesis",
            content=original_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(original_content, turn_id="t1"))
    original_memories = service.list_memories(_PRINCIPAL)
    assert len(original_memories) == 1
    original_id = original_memories[0].item.memory_id
    assert original_memories[0].item.memory_type == "thesis"

    # 2) 用户修订，model 抽成 risk（跨 type），content 含"调整" -> 跨 type replacement。
    revised_user_msg = (
        "我调整下对示例公司明年利润的判断，改为关注利润下行风险"
    )
    extractor.proposals = (
        candidate_proposal(
            revised_user_msg,
            subject="example-company-profit-risk",
            memory_type="risk",
            content=revised_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
            business_progress="monitoring",
        ),
    )
    revised_turn = TurnEnvelope(
        profile_id="investment-research",
        conversation_id="conversation-1",
        source_turn_id="t2",
        content=f"[user]\n{revised_user_msg}",
        observed_at=_NOW,
        subject_hint="example-company",
        messages=(
            TurnMessage(
                role=MessageRole.USER,
                content=revised_user_msg,
                message_id="message-t2",
            ),
        ),
    )
    result = service.capture_turn(_PRINCIPAL, revised_turn)
    # 跨 type 命中 -> semantic_cross_type_explicit_replacement，指向原记忆。
    replacement_outcomes = [
        o
        for o in result.outcomes
        if o.decision is AdmissionDecision.AUTO_SAVE
        and o.reason_code == "semantic_cross_type_explicit_replacement"
        and o.memory_id == original_id
    ]
    assert len(replacement_outcomes) == 1
    # 不新增第二条 active（原记忆被 superseded 且 type 同步成 risk）。
    active = service.list_memories(_PRINCIPAL)
    assert len(active) == 1
    assert active[0].item.memory_id == original_id
    # memory_type 已同步成新 type（risk），recall/去重不再按旧 thesis 错配。
    assert active[0].item.memory_type == "risk"


def test_cross_type_replacement_below_strong_match_does_not_replace() -> None:
    """跨 type top1 未达强匹配阈值（<0.75）-> 不跨 type 替，走 auto_save 新增。

    跨 type 替代比同 type 更激进，必须强匹配才敢替：跨 type 候选与旧记忆本就
    不同 type，仅"同主题但相关"（0.60~0.75）不足以判定为同一判断演进，保守
    保留独立判断，走 auto_save 新增。
    """

    original_content = "示例公司明年净利润大概率大幅增长"
    revised_content = "用户调整了对示例公司利润的看法"
    vectors = {
        original_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 original 相似度 ~0.70：>=0.60 fallback 但 <0.75 强匹配 -> 不跨 type 替。
        revised_content: (0.7, 0.714, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            original_content,
            subject="example-company-profit-thesis",
            memory_type="thesis",
            content=original_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(original_content, turn_id="t1"))
    original_id = service.list_memories(_PRINCIPAL)[0].item.memory_id

    revised_user_msg = "我调整下对示例公司利润的看法"
    extractor.proposals = (
        candidate_proposal(
            revised_user_msg,
            subject="example-company-profit-risk",
            memory_type="risk",
            content=revised_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t2",
            content=f"[user]\n{revised_user_msg}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=revised_user_msg,
                    message_id="message-t2",
                ),
            ),
        ),
    )
    # 不应触发跨 type 替代。
    cross_replacement = [
        o
        for o in result.outcomes
        if o.reason_code == "semantic_cross_type_explicit_replacement"
    ]
    assert len(cross_replacement) == 0
    # 旧 thesis 仍是 thesis（未被替代、type 未变）。
    active = service.list_memories(_PRINCIPAL)
    thesis = [m for m in active if m.item.memory_type == "thesis"]
    assert len(thesis) == 1
    assert thesis[0].item.memory_id == original_id


def test_cross_type_replacement_ambiguous_margin_pending() -> None:
    """跨 type top1 与 top2 margin 不足 -> 降 pending，不替错。

    跨 type top1 达强匹配但 top2 与 top1 太接近，无法确定替谁 ->
    ambiguous_cross_type_replacement_target 降 pending。
    """

    target_content = "示例公司明年利润判断：看好增长"
    sibling_content = "示例公司明年利润判断：关注下行风险"
    revised_content = "用户调整了对示例公司明年利润的判断"
    vectors = {
        # target / sibling 与 candidate 相似度都 ~1.0（>=0.75 强匹配），
        # 但 margin ~0 < 0.08 -> 无法确定替谁。
        target_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        sibling_content: (1.0, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        revised_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    # 两条不同 type 的旧记忆，subject 不同，都与 candidate 语义近似。
    extractor.proposals = (
        candidate_proposal(
            target_content,
            subject="example-company-profit-thesis",
            memory_type="thesis",
            content=target_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
        candidate_proposal(
            sibling_content,
            subject="example-company-profit-risk-sibling",
            memory_type="risk",
            content=sibling_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t1",
            content=f"[user]\n{target_content}。{sibling_content}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=f"{target_content}。{sibling_content}",
                    message_id="message-t1",
                ),
            ),
        ),
    )
    assert len(service.list_memories(_PRINCIPAL)) == 2

    # 候选抽成 research_question（跨 type），与两条旧记忆都近似且 margin 不足。
    revised_user_msg = "我调整下对示例公司明年利润的判断"
    extractor.proposals = (
        candidate_proposal(
            revised_user_msg,
            subject="example-company-profit-question",
            memory_type="research_question",
            content=revised_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t2",
            content=f"[user]\n{revised_user_msg}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=revised_user_msg,
                    message_id="message-t2",
                ),
            ),
        ),
    )
    pending = [
        o
        for o in result.outcomes
        if o.decision is AdmissionDecision.PENDING
        and o.reason_code == "ambiguous_cross_type_replacement_target"
    ]
    assert len(pending) == 1
    cross_replacement = [
        o
        for o in result.outcomes
        if o.reason_code == "semantic_cross_type_explicit_replacement"
    ]
    assert len(cross_replacement) == 0


def test_cross_type_scope_collision_handled_by_literal_path() -> None:
    """候选 subject 在新 type 下已有活动记忆 -> 字面路径先命中，不进跨 type fallback。

    跨 type fallback 仅在字面 find_current（同候选 subject+候选 type）无命中时
    触发。若候选的 (subject, type) 槽位已被占用，字面路径直接命中走同 type
    replacement，不会进跨 type 分支。本测试固化该边界，防止误把跨 type
    fallback 当成撞槽位的处理点。
    """

    original_content = "示例公司明年利润判断：看好增长"
    revised_content = "用户调整了对示例公司明年利润的判断"
    same_subject = "example-company-profit"
    vectors = {
        original_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        revised_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    # 建一条 risk（候选 subject），字面路径会命中它。
    extractor.proposals = (
        candidate_proposal(
            original_content,
            subject=same_subject,
            memory_type="risk",
            content=original_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(original_content, turn_id="t1"))
    risk_id = service.list_memories(_PRINCIPAL)[0].item.memory_id

    # 候选 subject 与旧 risk 相同、type 也同为 risk -> 字面路径命中，走同 type 替代。
    revised_user_msg = "我调整下对示例公司明年利润的判断"
    extractor.proposals = (
        candidate_proposal(
            revised_user_msg,
            subject=same_subject,
            memory_type="risk",
            content=revised_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t2",
            content=f"[user]\n{revised_user_msg}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=revised_user_msg,
                    message_id="message-t2",
                ),
            ),
        ),
    )
    # 字面路径命中 -> explicit_replacement（同 type），不进跨 type fallback。
    literal_replacement = [
        o
        for o in result.outcomes
        if o.reason_code == "explicit_replacement" and o.memory_id == risk_id
    ]
    assert len(literal_replacement) == 1
    cross_type = [
        o
        for o in result.outcomes
        if o.reason_code == "semantic_cross_type_explicit_replacement"
    ]
    assert len(cross_type) == 0


def test_cross_type_replacement_not_triggered_without_explicit_replacement() -> None:
    """非显式修订意图的跨 type 候选 -> 不触发跨 type fallback，走原新增路径。

    跨 type fallback 仅在 _is_explicit_replacement=True 时触发。普通跨 type
    候选（用户随口提及相关但不同 type 的判断，无"改成/调整"意图词）应保留
    独立判断，不跨 type 误替。
    """

    original_content = "示例公司明年净利润大概率大幅增长"
    related_content = "示例公司明年利润可能面临下行压力"
    vectors = {
        original_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        # 与 original 相似度 1.0（远超强匹配），但无显式修订意图词。
        related_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            original_content,
            subject="example-company-profit-thesis",
            memory_type="thesis",
            content=original_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(original_content, turn_id="t1"))

    # 无"改成/调整/修正"等显式修订词，仅陈述一条相关 risk。
    related_user_msg = "示例公司明年利润可能面临下行压力"
    extractor.proposals = (
        candidate_proposal(
            related_user_msg,
            subject="example-company-profit-risk",
            memory_type="risk",
            content=related_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t2",
            content=f"[user]\n{related_user_msg}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=related_user_msg,
                    message_id="message-t2",
                ),
            ),
        ),
    )
    # 不应触发跨 type 替代，risk 应作为独立判断 auto_save 新增。
    cross_replacement = [
        o
        for o in result.outcomes
        if o.reason_code == "semantic_cross_type_explicit_replacement"
    ]
    assert len(cross_replacement) == 0
    active = service.list_memories(_PRINCIPAL)
    # 旧 thesis + 新 risk 两条共存（保留独立判断）。
    assert len(active) == 2
    types = {m.item.memory_type for m in active}
    assert types == {"thesis", "risk"}


def test_same_type_replacement_does_not_trigger_cross_type_branch() -> None:
    """同 type fallback 命中时不走跨 type 分支（回归保护）。

    确认跨 type fallback 是第二级：同 type fallback 命中时直接走同 type
    replacement，reason_code 仍为 semantic_explicit_replacement（非跨 type 版）。
    """

    original_content = "示例公司出海是结构性的，海外占比持续提升"
    revised_content = "用户修订了示例公司出海判断标准：不能只看海外增速"
    vectors = {
        original_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        revised_content: (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    }
    service, extractor = _service(vectors)
    extractor.proposals = (
        candidate_proposal(
            original_content,
            subject="example-company-oversea-thesis",
            memory_type="thesis",
            content=original_content,
            assertion_kind=AssertionKind.USER_VIEW,
            business_progress="monitoring",
        ),
    )
    service.capture_turn(_PRINCIPAL, _turn(original_content, turn_id="t1"))
    original_id = service.list_memories(_PRINCIPAL)[0].item.memory_id

    revised_user_msg = "我改一下最开始那个判断。以后判断示例公司出海不能只看海外增速"
    extractor.proposals = (
        candidate_proposal(
            "以后判断示例公司出海不能只看海外增速",
            subject="example-company-oversea-thesis-revised",
            memory_type="thesis",  # 同 type
            content=revised_content,
            assertion_kind=AssertionKind.USER_VIEW,
            expression_basis=ExpressionBasis.EXPLICIT,
            business_progress="monitoring",
        ),
    )
    result = service.capture_turn(
        _PRINCIPAL,
        TurnEnvelope(
            profile_id="investment-research",
            conversation_id="conversation-1",
            source_turn_id="t2",
            content=f"[user]\n{revised_user_msg}",
            observed_at=_NOW,
            subject_hint="example-company",
            messages=(
                TurnMessage(
                    role=MessageRole.USER,
                    content=revised_user_msg,
                    message_id="message-t2",
                ),
            ),
        ),
    )
    # 同 type -> semantic_explicit_replacement（非跨 type 版），memory_type 不变。
    same_type = [
        o
        for o in result.outcomes
        if o.reason_code == "semantic_explicit_replacement"
        and o.memory_id == original_id
    ]
    assert len(same_type) == 1
    cross_type = [
        o
        for o in result.outcomes
        if o.reason_code == "semantic_cross_type_explicit_replacement"
    ]
    assert len(cross_type) == 0
    active = service.list_memories(_PRINCIPAL)
    assert len(active) == 1
    assert active[0].item.memory_type == "thesis"
