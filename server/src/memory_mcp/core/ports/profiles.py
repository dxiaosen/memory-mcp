"""记忆配置契约和进程内注册表。"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from memory_mcp.core.domain.models import SensitivityLevel
from memory_mcp.core.exceptions import (
    InvalidMemoryProfileError,
    InvalidMemoryRelationError,
    InvalidMemoryTypeError,
    InvalidProfileProgressError,
    ProfileAlreadyRegisteredError,
    ProfileNotRegisteredError,
)


@dataclass(frozen=True, slots=True)
class MemoryMetadataPolicy:
    """一种 memory type 的通用元数据默认规则。"""

    sensitivity_level: SensitivityLevel = SensitivityLevel.CONFIDENTIAL
    validity_days: int | None = None
    # 语义去重阈值（余弦相似度下界，>= 该值视为同一条记忆）。None 表示该
    # 类型不启用基于嵌入的语义去重；准入阶段仅在 threshold 非 None 且嵌入
    # 可用时触发，避免字面不同但语义重复的候选造成记忆碎片化。
    semantic_dedup_threshold: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sensitivity_level, SensitivityLevel):
            raise ValueError("sensitivity_level must be a SensitivityLevel")
        if self.validity_days is not None and self.validity_days < 1:
            raise ValueError("validity_days must be positive")
        if self.semantic_dedup_threshold is not None and not (
            0.0 < self.semantic_dedup_threshold < 1.0
        ):
            raise ValueError("semantic_dedup_threshold must be within (0, 1)")


@dataclass(frozen=True, slots=True)
class MemoryExpiryDerivation:
    """当某关系端点到期时派生一条提醒记忆的声明。

    由 Profile 声明、维护循环消费：``trigger_relation_types`` 指明哪些关系类型
    的端点到期会触发提醒；``reminder_memory_type`` 是派生记忆的类型；
    ``reminder_template`` 支持占位符 ``{endpoint_subject}`` 和 ``{thesis_subject}``。
    """

    trigger_relation_types: frozenset[str]
    reminder_memory_type: str
    reminder_template: str

    def __post_init__(self) -> None:
        if not _contains_only_normalized_text(self.trigger_relation_types):
            raise ValueError("trigger_relation_types must contain normalized text")
        if not self.trigger_relation_types:
            raise ValueError("trigger_relation_types must not be empty")
        if not isinstance(self.reminder_memory_type, str) or not self.reminder_memory_type:
            raise ValueError("reminder_memory_type must not be empty")
        if (
            not isinstance(self.reminder_template, str)
            or not self.reminder_template
            or self.reminder_template != self.reminder_template.strip()
        ):
            raise ValueError("reminder_template must be normalized non-empty text")


@dataclass(frozen=True, slots=True)
class MemoryRelationPolicy:
    """一种关系允许连接的记忆类型及其稳定语义。"""

    source_memory_types: frozenset[str]
    target_memory_types: frozenset[str]
    description: str
    direction_cues: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not _contains_only_normalized_text(self.source_memory_types):
            raise ValueError("source_memory_types must contain normalized text")
        if not self.source_memory_types:
            raise ValueError("source_memory_types must not be empty")
        if not _contains_only_normalized_text(self.target_memory_types):
            raise ValueError("target_memory_types must contain normalized text")
        if not self.target_memory_types:
            raise ValueError("target_memory_types must not be empty")
        if (
            not isinstance(self.description, str)
            or not self.description
            or self.description != self.description.strip()
        ):
            raise ValueError("description must be normalized non-empty text")
        if not _contains_only_normalized_text(self.direction_cues):
            raise ValueError("direction_cues must contain normalized text")


class MemoryProfile(Protocol):
    """一套记忆配置只能通过该接口向 Core 声明业务差异。"""

    @property
    def profile_id(self) -> str:
        """返回全局唯一的记忆配置标识。"""

        ...

    @property
    def memory_types(self) -> frozenset[str]:
        """返回当前配置允许的原子记忆类型。"""

        ...

    @property
    def business_progress_values(self) -> frozenset[str]:
        """返回当前配置允许的业务进展值；可为空。"""

        ...

    @property
    def capture_guidance(self) -> str:
        """返回捕获阶段使用的配置说明。"""

        ...

    @property
    def profile_version(self) -> str:
        """返回当前配置捕获规则的稳定版本。"""

        ...

    @property
    def relation_policies(self) -> Mapping[str, MemoryRelationPolicy]:
        """返回关系名称到合法端点策略的映射；可为空。"""

        ...

    @property
    def recall_priorities(self) -> Mapping[str, int]:
        """返回后续召回阶段使用的类型优先级。"""

        ...

    @property
    def recall_hints(self) -> Mapping[str, frozenset[str]]:
        """返回各记忆类型对应的查询语义提示；值可为空集合。"""

        ...

    @property
    def metadata_policies(self) -> Mapping[str, MemoryMetadataPolicy]:
        """返回每种合法 memory type 的敏感和有效期默认规则。"""

        ...

    @property
    def timeline_relation_types(self) -> frozenset[str]:
        """返回时间线召回允许遍历的关系类型；空集合表示该 Profile 不启用时间线。"""

        ...

    @property
    def expiry_derivations(self) -> Mapping[str, MemoryExpiryDerivation]:
        """返回关系类型到到期派生规则的映射；空映射表示不启用到期提醒。"""

        ...


class ProfileRegistry:
    """显式注册并校验记忆配置，不提供隐式默认配置。"""

    def __init__(self) -> None:
        self._profiles: dict[str, MemoryProfile] = {}

    def validate_registration(self, profile: MemoryProfile) -> None:
        """在不改变注册表的情况下校验一项新配置。"""

        profile_id = profile.profile_id
        if not isinstance(profile_id, str) or profile_id != profile_id.strip():
            raise InvalidMemoryProfileError("profile_id must not be empty")
        if not profile_id:
            raise InvalidMemoryProfileError("profile_id must not be empty")
        if not _contains_only_normalized_text(profile.memory_types):
            raise InvalidMemoryProfileError(
                "memory_types must contain non-empty values"
            )
        if not profile.memory_types:
            raise InvalidMemoryProfileError("memory_types must not be empty")
        if not _contains_only_normalized_text(profile.business_progress_values):
            raise InvalidMemoryProfileError(
                "business_progress_values must contain non-empty values"
            )
        if (
            not isinstance(profile.capture_guidance, str)
            or not profile.capture_guidance
            or profile.capture_guidance != profile.capture_guidance.strip()
        ):
            raise InvalidMemoryProfileError("capture_guidance must not be empty")
        if (
            not isinstance(profile.profile_version, str)
            or not profile.profile_version
            or profile.profile_version != profile.profile_version.strip()
        ):
            raise InvalidMemoryProfileError("profile_version must not be empty")
        if set(profile.metadata_policies) != set(profile.memory_types):
            raise InvalidMemoryProfileError(
                "metadata_policies must define every memory type exactly once"
            )
        if any(
            not isinstance(policy, MemoryMetadataPolicy)
            for policy in profile.metadata_policies.values()
        ):
            raise InvalidMemoryProfileError(
                "metadata_policies must contain MemoryMetadataPolicy values"
            )
        if set(profile.recall_priorities) != set(profile.memory_types):
            raise InvalidMemoryProfileError(
                "recall_priorities must define every memory type exactly once"
            )
        if any(
            isinstance(priority, bool) or not isinstance(priority, int) or priority < 0
            for priority in profile.recall_priorities.values()
        ):
            raise InvalidMemoryProfileError(
                "recall_priorities must contain non-negative integers"
            )
        if set(profile.recall_hints) != set(profile.memory_types):
            raise InvalidMemoryProfileError(
                "recall_hints must define every memory type exactly once"
            )
        if any(
            not isinstance(hints, frozenset)
            or not _contains_only_normalized_text(hints)
            for hints in profile.recall_hints.values()
        ):
            raise InvalidMemoryProfileError(
                "recall_hints must contain frozensets of normalized text"
            )
        if not _contains_only_normalized_text(frozenset(profile.relation_policies)):
            raise InvalidMemoryProfileError(
                "relation_policies must use normalized non-empty keys"
            )
        for relation_type, policy in profile.relation_policies.items():
            if not isinstance(policy, MemoryRelationPolicy):
                raise InvalidMemoryProfileError(
                    "relation_policies must contain MemoryRelationPolicy values"
                )
            unknown_types = (
                policy.source_memory_types | policy.target_memory_types
            ) - profile.memory_types
            if unknown_types:
                raise InvalidMemoryProfileError(
                    f"relation policy {relation_type} references unknown memory types"
                )
        _validate_optional_timeline(profile)
        _validate_optional_expiry_derivations(profile)
        if profile_id in self._profiles:
            raise ProfileAlreadyRegisteredError(
                f"profile_id already registered: {profile_id}"
            )

    def register(self, profile: MemoryProfile) -> None:
        """校验并将配置加入当前进程注册表。"""

        self.validate_registration(profile)
        profile_id = profile.profile_id
        self._profiles[profile_id] = profile

    def get(self, profile_id: str) -> MemoryProfile:
        """返回已注册的配置；未注册时抛 ProfileNotRegisteredError。"""

        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ProfileNotRegisteredError(
                f"profile_id is not registered: {profile_id}"
            ) from exc

    def validate_memory_type(self, profile_id: str, memory_type: str) -> None:
        """校验 memory_type 是否被该 Profile 允许，否则抛 InvalidMemoryTypeError。"""

        profile = self.get(profile_id)
        if memory_type not in profile.memory_types:
            raise InvalidMemoryTypeError(
                f"memory type is not allowed by profile_id {profile_id}: {memory_type}"
            )

    def validate_business_progress(
        self,
        profile_id: str,
        business_progress: str | None,
    ) -> None:
        """校验 business_progress 是否被该 Profile 允许；None 表示未提供，直接通过。"""

        if business_progress is None:
            return
        profile = self.get(profile_id)
        if business_progress not in profile.business_progress_values:
            raise InvalidProfileProgressError(
                "business progress is not allowed by profile_id "
                f"{profile_id}: {business_progress}"
            )

    def metadata_policy(
        self,
        profile_id: str,
        memory_type: str,
    ) -> MemoryMetadataPolicy:
        """取得已校验 profile 下某种记忆的元数据规则。"""

        self.validate_memory_type(profile_id, memory_type)
        return self.get(profile_id).metadata_policies[memory_type]

    def validate_relation(
        self,
        profile_id: str,
        relation_type: str,
        source_memory_type: str,
        target_memory_type: str,
    ) -> None:
        """校验关系名称和有向端点类型。"""

        profile = self.get(profile_id)
        policy = profile.relation_policies.get(relation_type)
        if policy is None:
            raise InvalidMemoryRelationError(
                f"relation type is not allowed by profile_id {profile_id}"
            )
        if (
            source_memory_type not in policy.source_memory_types
            or target_memory_type not in policy.target_memory_types
        ):
            raise InvalidMemoryRelationError(
                f"relation endpoints are not allowed for {relation_type}"
            )

    @property
    def profile_ids(self) -> frozenset[str]:
        """返回已注册标识的不可变快照。"""

        return frozenset(self._profiles)


def _expiry_derivations_payload(profile: MemoryProfile) -> dict[str, dict[str, object]]:
    """把 expiry_derivations（可能未声明）序列化为可哈希的稳定结构。"""

    derivations = getattr(profile, "expiry_derivations", {})
    if not derivations:
        return {}
    return {
        key: {
            "reminder_memory_type": derivation.reminder_memory_type,
            "reminder_template": derivation.reminder_template,
            "trigger_relation_types": sorted(derivation.trigger_relation_types),
        }
        for key, derivation in sorted(derivations.items())
    }


def profile_fingerprint(profile: MemoryProfile) -> str:
    """为会影响记忆行为的 Profile 声明生成稳定 SHA-256 指纹。"""

    payload = {
        "business_progress_values": sorted(profile.business_progress_values),
        "capture_guidance": profile.capture_guidance,
        "expiry_derivations": _expiry_derivations_payload(profile),
        "memory_types": sorted(profile.memory_types),
        "metadata_policies": {
            memory_type: {
                "sensitivity_level": policy.sensitivity_level.value,
                "semantic_dedup_threshold": policy.semantic_dedup_threshold,
                "validity_days": policy.validity_days,
            }
            for memory_type, policy in sorted(profile.metadata_policies.items())
        },
        "profile_id": profile.profile_id,
        "recall_hints": {
            memory_type: sorted(hints)
            for memory_type, hints in sorted(profile.recall_hints.items())
        },
        "recall_priorities": dict(sorted(profile.recall_priorities.items())),
        "relation_policies": {
            relation_type: {
                "description": policy.description,
                "direction_cues": sorted(policy.direction_cues),
                "source_memory_types": sorted(policy.source_memory_types),
                "target_memory_types": sorted(policy.target_memory_types),
            }
            for relation_type, policy in sorted(profile.relation_policies.items())
        },
        "timeline_relation_types": sorted(
            getattr(profile, "timeline_relation_types", frozenset()) or frozenset()
        ),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _contains_only_normalized_text(values: frozenset[str]) -> bool:
    return all(
        isinstance(value, str) and bool(value) and value == value.strip()
        for value in values
    )


def _validate_optional_timeline(profile: MemoryProfile) -> None:
    """校验 timeline_relation_types 可选属性：存在时须是已声明的关系类型子集。"""

    timeline = getattr(profile, "timeline_relation_types", frozenset())
    if timeline is None:
        return
    if not isinstance(timeline, frozenset):
        raise InvalidMemoryProfileError(
            "timeline_relation_types must be a frozenset"
        )
    if not _contains_only_normalized_text(timeline):
        raise InvalidMemoryProfileError(
            "timeline_relation_types must contain normalized text"
        )
    unknown = set(timeline) - set(profile.relation_policies)
    if unknown:
        raise InvalidMemoryProfileError(
            "timeline_relation_types must be a subset of relation_policies"
        )


def _validate_optional_expiry_derivations(profile: MemoryProfile) -> None:
    """校验 expiry_derivations 可选属性：触发关系类型须在 relation_policies 内，
    派生记忆类型须在 memory_types 内。"""

    derivations = getattr(profile, "expiry_derivations", {})
    if not derivations:
        return
    if not isinstance(derivations, Mapping):
        raise InvalidMemoryProfileError(
            "expiry_derivations must be a Mapping"
        )
    for key, derivation in derivations.items():
        if not isinstance(derivation, MemoryExpiryDerivation):
            raise InvalidMemoryProfileError(
                "expiry_derivations must contain MemoryExpiryDerivation values"
            )
        if not isinstance(key, str) or not key:
            raise InvalidMemoryProfileError(
                "expiry_derivations keys must be non-empty strings"
            )
        unknown_relations = set(derivation.trigger_relation_types) - set(
            profile.relation_policies
        )
        if unknown_relations:
            raise InvalidMemoryProfileError(
                "expiry_derivations trigger_relation_types must be a subset "
                "of relation_policies"
            )
        if derivation.reminder_memory_type not in profile.memory_types:
            raise InvalidMemoryProfileError(
                "expiry_derivations reminder_memory_type must be a known memory type"
            )
