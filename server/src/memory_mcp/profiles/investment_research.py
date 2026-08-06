"""面向投研工作的正式记忆配置。"""

from dataclasses import dataclass, field

from memory_mcp.core import (
    MemoryMetadataPolicy,
    MemoryRelationPolicy,
    SensitivityLevel,
)


@dataclass(frozen=True, slots=True)
class InvestmentResearchProfile:
    """声明投研记忆的原子类型、有效期和召回优先级。"""

    profile_id: str = "investment-research"
    memory_types: frozenset[str] = frozenset(
        {
            "research_preference",
            "research_question",
            "thesis",
            "evidence_claim",
            "risk",
            "catalyst",
            "ongoing_research",
            "research_decision",
        }
    )
    business_progress_values: frozenset[str] = frozenset(
        {"open", "monitoring", "resolved", "invalidated", "archived"}
    )
    capture_guidance: str = (
        "Capture only durable investment-research context. Use "
        "research_preference for the user's lasting research or output methods; "
        "research_question for an unresolved question; thesis for one explicit, "
        "falsifiable user view; evidence_claim for one externally sourced claim; "
        "risk for one factor that may invalidate a thesis; catalyst for one future "
        "event; ongoing_research for one cross-session research task or gap; and "
        "research_decision only for a research scope, method, or conclusion, never "
        "a trade instruction. Keep each candidate independently replaceable. Make "
        "subject identify the entity or theme plus the exact metric, period, event, "
        "question, or thesis focus so unrelated claims can coexist. Use external_fact "
        "for evidence_claim and user_view for a user thesis. Leave business_progress "
        "empty unless the source explicitly states open, monitoring, resolved, "
        "invalidated, or archived. Never capture credentials, real holdings, orders, "
        "or buy/sell instructions. Prefer no candidate when the type or durability is "
        "ambiguous."
    )
    profile_version: str = "v1"
    relation_policies: dict[str, MemoryRelationPolicy] = field(
        default_factory=lambda: {
            "supports": MemoryRelationPolicy(
                frozenset({"evidence_claim"}),
                frozenset({"thesis"}),
                "Externally sourced evidence supports a thesis.",
                frozenset({"支持", "support", "supports"}),
            ),
            "challenges": MemoryRelationPolicy(
                frozenset({"evidence_claim"}),
                frozenset({"thesis"}),
                "Externally sourced evidence challenges a thesis.",
                frozenset({"挑战", "challenge", "challenges"}),
            ),
            "threatens": MemoryRelationPolicy(
                frozenset({"risk"}),
                frozenset({"thesis"}),
                "A risk may invalidate or weaken a thesis.",
                frozenset({"威胁", "threaten", "threatens"}),
            ),
            "could_catalyze": MemoryRelationPolicy(
                frozenset({"catalyst"}),
                frozenset({"thesis"}),
                "A future event may change how a thesis develops.",
                frozenset({"催化", "catalyze", "catalyzes"}),
            ),
            "addresses": MemoryRelationPolicy(
                frozenset({"ongoing_research"}),
                frozenset({"research_question"}),
                "An ongoing research task addresses an open question.",
                frozenset({"回答", "address", "addresses"}),
            ),
            "resolves": MemoryRelationPolicy(
                frozenset({"research_decision"}),
                frozenset({"research_question"}),
                "A research conclusion resolves an open question.",
                frozenset({"解决", "resolve", "resolves"}),
            ),
        }
    )
    recall_priorities: dict[str, int] = field(
        default_factory=lambda: {
            "research_preference": 50,
            "research_decision": 45,
            "thesis": 40,
            "risk": 38,
            "ongoing_research": 35,
            "research_question": 34,
            "catalyst": 32,
            "evidence_claim": 30,
        }
    )
    recall_hints: dict[str, frozenset[str]] = field(
        default_factory=lambda: {
            "research_preference": frozenset(
                {"偏好", "习惯", "格式", "怎么写", "如何组织", "模板"}
            ),
            "research_question": frozenset(
                {
                    "问题",
                    "待确认",
                    "是否",
                    "未解决",
                    "需要核实",
                    "验证",
                    "跟踪",
                }
            ),
            "thesis": frozenset(
                {"论点", "判断", "核心看法", "逻辑", "估值", "目标价", "预测"}
            ),
            "evidence_claim": frozenset(
                {
                    "证据",
                    "依据",
                    "数据",
                    "披露",
                    "季报",
                    "年报",
                    "调研纪要",
                    "路演",
                }
            ),
            "risk": frozenset(
                {"风险", "威胁", "破坏", "不利因素", "竞品", "产能过剩"}
            ),
            "catalyst": frozenset(
                {"催化", "事件", "推动", "加速", "投产", "放量"}
            ),
            "ongoing_research": frozenset(
                {
                    "下一步",
                    "后续",
                    "继续",
                    "调研",
                    "访谈",
                    "跟进",
                    "还要做",
                    "路演",
                }
            ),
            "research_decision": frozenset(
                {"决定", "最终", "怎么定", "范围", "结论", "是否纳入"}
            ),
        }
    )
    metadata_policies: dict[str, MemoryMetadataPolicy] = field(
        default_factory=lambda: {
            "research_preference": MemoryMetadataPolicy(),
            "research_question": MemoryMetadataPolicy(validity_days=365),
            "thesis": MemoryMetadataPolicy(validity_days=180),
            "evidence_claim": MemoryMetadataPolicy(
                sensitivity_level=SensitivityLevel.INTERNAL,
                validity_days=90,
            ),
            "risk": MemoryMetadataPolicy(validity_days=180),
            "catalyst": MemoryMetadataPolicy(
                sensitivity_level=SensitivityLevel.INTERNAL,
                validity_days=90,
            ),
            "ongoing_research": MemoryMetadataPolicy(validity_days=365),
            "research_decision": MemoryMetadataPolicy(),
        }
    )
