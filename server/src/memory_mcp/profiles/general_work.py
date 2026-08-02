"""适用于跨 Agent 通用工作的默认记忆配置。"""

from dataclasses import dataclass, field

from memory_mcp.core import MemoryMetadataPolicy, MemoryRelationPolicy


@dataclass(frozen=True, slots=True)
class GeneralWorkProfile:
    """声明通用工作的类型、捕获说明和轻量召回优先级。"""

    profile_id: str = "general-work"
    memory_types: frozenset[str] = frozenset(
        {"preference", "stable_context", "ongoing_item", "decision"}
    )
    business_progress_values: frozenset[str] = frozenset()
    capture_guidance: str = (
        "Capture explicit durable user preferences, stable context, ongoing "
        "items, and decisions. Keep ambiguous or inferred changes pending."
    )
    profile_version: str = "general-work-v2"
    relation_policies: dict[str, MemoryRelationPolicy] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(
        default_factory=lambda: {
            "preference": 40,
            "decision": 35,
            "ongoing_item": 30,
            "stable_context": 20,
        }
    )
    recall_hints: dict[str, frozenset[str]] = field(
        default_factory=lambda: {
            "preference": frozenset({"偏好", "习惯", "默认", "格式", "prefer"}),
            "stable_context": frozenset({"背景", "现状", "环境", "context"}),
            "ongoing_item": frozenset(
                {"下一步", "继续", "跟进", "待办", "计划", "follow up"}
            ),
            "decision": frozenset({"决定", "决策", "最终", "确定", "decided"}),
        }
    )
    metadata_policies: dict[str, MemoryMetadataPolicy] = field(
        default_factory=lambda: {
            memory_type: MemoryMetadataPolicy()
            for memory_type in (
                "preference",
                "stable_context",
                "ongoing_item",
                "decision",
            )
        }
    )
