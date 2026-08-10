"""不依赖具体记忆配置业务词义的确定性候选准入策略：保守、可解释。"""

import re
from dataclasses import dataclass

from memory_mcp.core.domain import (
    AdmissionDecision,
    AssertionKind,
    Candidate,
    CandidateDurability,
    ExpressionBasis,
)

# 用户明确表达不确定/猜测/未验证。只判断 source_expression 与 content
# 邻近原文，确定性函数，不做语义模糊匹配。命中 -> PENDING(explicit_uncertainty)，
# 优先级高于 explicit_durable_statement：explicit uncertainty > explicit durable。
# 注意：「可能」单独出现时中文歧义（可能推动=can drive vs 可能是=maybe），需限定为
# 不确定语境（「可能...但」「只是可能」「只是猜测/也许/暂/不确定/没有证据」等明确表达）。
_EXPLICIT_UNCERTAINTY_RE = re.compile(
    r"(?:只是?猜测|仅仅是?假设|只是?可能|也许|或许|猜测|暂[时定]|不确定|未经验证|"
    r"没有足够证据|缺乏证据|不能确认|尚不能确认|不要当成已确认|不要作为已确认|"
    r"只是一个假设|只是猜测|"
    r"\bmaybe\b|\bmight\b|\bpossibly\b|\buncertain\b|\bunverified\b|"
    r"\bnot enough evidence\b|\bdo not treat as confirmed\b|\bhypothesis\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    """程序规则给出的准入决策及其稳定原因码。"""

    decision: AdmissionDecision
    reason_code: str


class ConservativeAdmissionPolicy:
    """保守准入策略：仅当内容显式、高置信且持久时才允许自动保存。

    临时或不确定内容直接丢弃；非显式、系统推断或低置信内容降级为待确认。
    """

    def __init__(self, *, auto_save_confidence: float = 0.9) -> None:
        if not 0.0 <= auto_save_confidence <= 1.0:
            raise ValueError("auto_save_confidence must be between 0 and 1")
        self._auto_save_confidence = auto_save_confidence

    def decide(self, candidate: Candidate) -> AdmissionOutcome:
        """按持久性 -> 不确定性 -> 断言来源 -> 显式性 -> 置信度的顺序判定准入决策。

        explicit uncertainty 优先于 explicit durable：用户明确表达
        猜测/未验证时，即使 explicit + durable + 高置信也降级为 Pending，不进入 Active。
        """
        if candidate.durability is CandidateDurability.TEMPORARY:
            return AdmissionOutcome(
                AdmissionDecision.DISCARD,
                "temporary_content",
            )
        if candidate.durability is CandidateDurability.UNCERTAIN:
            return AdmissionOutcome(
                AdmissionDecision.PENDING,
                "uncertain_durability",
            )
        if has_explicit_uncertainty(candidate):
            return AdmissionOutcome(
                AdmissionDecision.PENDING,
                "explicit_uncertainty",
            )
        if candidate.assertion_kind is AssertionKind.SYSTEM_INFERENCE:
            return AdmissionOutcome(
                AdmissionDecision.PENDING,
                "system_inference",
            )
        if candidate.expression_basis is not ExpressionBasis.EXPLICIT:
            return AdmissionOutcome(
                AdmissionDecision.PENDING,
                "non_explicit_expression",
            )
        if candidate.confidence < self._auto_save_confidence:
            return AdmissionOutcome(
                AdmissionDecision.PENDING,
                "low_confidence",
            )
        return AdmissionOutcome(
            AdmissionDecision.AUTO_SAVE,
            "explicit_durable_statement",
        )


def has_explicit_uncertainty(candidate: Candidate) -> bool:
    """Candidate 的 source_expression/content 是否表达明确不确定/猜测/未验证。

    只判断邻近原文，确定性，不做语义模糊匹配。
    """

    return _EXPLICIT_UNCERTAINTY_RE.search(candidate.source_expression) is not None or (
        _EXPLICIT_UNCERTAINTY_RE.search(candidate.content) is not None
    )
