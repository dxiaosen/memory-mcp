"""场景扩展契约和进程内注册表。"""

from collections.abc import Mapping
from typing import Protocol

from agent_lab.memory.exceptions import (
    InvalidMemoryTypeError,
    InvalidScenarioPolicyError,
    InvalidScenarioProgressError,
    ScenarioAlreadyRegisteredError,
    ScenarioNotRegisteredError,
)


class ScenarioPolicy(Protocol):
    """具体场景只能通过该接口向 Core 声明业务差异。"""

    @property
    def scenario_id(self) -> str:
        """返回全局唯一的场景标识。"""

        ...

    @property
    def memory_types(self) -> frozenset[str]:
        """返回场景允许的原子记忆类型。"""

        ...

    @property
    def business_progress_values(self) -> frozenset[str]:
        """返回场景允许的业务进展值；可为空。"""

        ...

    @property
    def allowed_relations(self) -> frozenset[str]:
        """返回场景允许建立的业务关系；可为空。"""

        ...

    @property
    def capture_guidance(self) -> str:
        """返回捕获阶段使用的场景说明。"""

        ...

    @property
    def policy_version(self) -> str:
        """返回场景捕获规则的稳定版本。"""

        ...

    @property
    def relation_rules(self) -> Mapping[str, str]:
        """返回后续关系判断阶段使用的规则说明。"""

        ...

    @property
    def recall_priorities(self) -> Mapping[str, int]:
        """返回后续召回阶段使用的类型优先级。"""

        ...


class ScenarioRegistry:
    """显式注册并校验场景策略，不提供隐式默认场景。"""

    def __init__(self) -> None:
        self._policies: dict[str, ScenarioPolicy] = {}

    def validate_registration(self, policy: ScenarioPolicy) -> None:
        """在不改变注册表的情况下校验一项新策略。"""

        scenario_id = policy.scenario_id
        if not isinstance(scenario_id, str) or scenario_id != scenario_id.strip():
            raise InvalidScenarioPolicyError("scenario_id must not be empty")
        if not scenario_id:
            raise InvalidScenarioPolicyError("scenario_id must not be empty")
        if not _contains_only_normalized_text(policy.memory_types):
            raise InvalidScenarioPolicyError(
                "memory_types must contain non-empty values"
            )
        if not policy.memory_types:
            raise InvalidScenarioPolicyError("memory_types must not be empty")
        if not _contains_only_normalized_text(policy.business_progress_values):
            raise InvalidScenarioPolicyError(
                "business_progress_values must contain non-empty values"
            )
        if not _contains_only_normalized_text(policy.allowed_relations):
            raise InvalidScenarioPolicyError(
                "allowed_relations must contain non-empty values"
            )
        if (
            not isinstance(policy.capture_guidance, str)
            or not policy.capture_guidance
            or policy.capture_guidance != policy.capture_guidance.strip()
        ):
            raise InvalidScenarioPolicyError("capture_guidance must not be empty")
        if (
            not isinstance(policy.policy_version, str)
            or not policy.policy_version
            or policy.policy_version != policy.policy_version.strip()
        ):
            raise InvalidScenarioPolicyError("policy_version must not be empty")
        if scenario_id in self._policies:
            raise ScenarioAlreadyRegisteredError(
                f"scenario already registered: {scenario_id}"
            )

    def register(self, policy: ScenarioPolicy) -> None:
        """校验并将策略加入当前进程注册表。"""

        self.validate_registration(policy)
        scenario_id = policy.scenario_id
        self._policies[scenario_id] = policy

    def get(self, scenario_id: str) -> ScenarioPolicy:
        try:
            return self._policies[scenario_id]
        except KeyError as exc:
            raise ScenarioNotRegisteredError(
                f"scenario is not registered: {scenario_id}"
            ) from exc

    def validate_memory_type(self, scenario_id: str, memory_type: str) -> None:
        policy = self.get(scenario_id)
        if memory_type not in policy.memory_types:
            raise InvalidMemoryTypeError(
                f"memory type is not allowed by scenario {scenario_id}: {memory_type}"
            )

    def validate_business_progress(
        self,
        scenario_id: str,
        business_progress: str | None,
    ) -> None:
        if business_progress is None:
            return
        policy = self.get(scenario_id)
        if business_progress not in policy.business_progress_values:
            raise InvalidScenarioProgressError(
                "business progress is not allowed by scenario "
                f"{scenario_id}: {business_progress}"
            )

    @property
    def scenario_ids(self) -> frozenset[str]:
        """返回已注册标识的不可变快照。"""

        return frozenset(self._policies)


def _contains_only_normalized_text(values: frozenset[str]) -> bool:
    return all(
        isinstance(value, str) and bool(value) and value == value.strip()
        for value in values
    )
