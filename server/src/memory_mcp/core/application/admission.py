"""不依赖具体记忆配置业务词义的确定性候选准入策略：保守、可解释。"""

from dataclasses import dataclass

from memory_mcp.core.domain import (
    AdmissionDecision,
    AssertionKind,
    Candidate,
    CandidateDurability,
    ExpressionBasis,
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

    def __init__(self, *, auto_save_confidence: float = 0.8) -> None:
        if not 0.0 <= auto_save_confidence <= 1.0:
            raise ValueError("auto_save_confidence must be between 0 and 1")
        self._auto_save_confidence = auto_save_confidence

    def decide(self, candidate: Candidate) -> AdmissionOutcome:
        """按持久性 -> 断言来源 -> 显式性 -> 置信度的顺序判定准入决策。"""
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
