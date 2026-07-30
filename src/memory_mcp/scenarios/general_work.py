"""适用于跨 Agent 通用工作的最小正式场景。"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GeneralWorkPolicy:
    """声明通用工作的类型、捕获说明和轻量召回优先级。"""

    scenario_id: str = "general-work"
    memory_types: frozenset[str] = frozenset(
        {"preference", "stable_context", "ongoing_item", "decision"}
    )
    business_progress_values: frozenset[str] = frozenset()
    allowed_relations: frozenset[str] = frozenset()
    capture_guidance: str = (
        "Capture explicit durable user preferences, stable context, ongoing "
        "items, and decisions. Keep ambiguous or inferred changes pending."
    )
    policy_version: str = "general-work-v1"
    relation_rules: dict[str, str] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(
        default_factory=lambda: {
            "preference": 40,
            "decision": 35,
            "ongoing_item": 30,
            "stable_context": 20,
        }
    )
