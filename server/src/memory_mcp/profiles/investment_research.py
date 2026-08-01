"""面向投研工作的正式记忆配置。"""

from dataclasses import dataclass, field

from memory_mcp.core import MemoryMetadataPolicy, SensitivityLevel


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
    # Core 尚未提供可持久化关系契约，不能提前声明看似可用的关系。
    allowed_relations: frozenset[str] = frozenset()
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
    profile_version: str = "investment-research-v1"
    relation_rules: dict[str, str] = field(default_factory=dict)
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
