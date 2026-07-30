"""Configurable stage-three policy used until formal scenarios are introduced."""

from dataclasses import dataclass, field

from memory_mcp.server.settings import MemoryServerSettings


@dataclass(frozen=True, slots=True)
class ConfiguredScenarioPolicy:
    """A small policy that keeps scenario vocabulary outside Memory Core."""

    scenario_id: str
    memory_types: frozenset[str]
    business_progress_values: frozenset[str]
    capture_guidance: str
    policy_version: str
    allowed_relations: frozenset[str] = frozenset()
    relation_rules: dict[str, str] = field(default_factory=dict)
    recall_priorities: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_settings(
        cls,
        settings: MemoryServerSettings,
    ) -> ConfiguredScenarioPolicy:
        return cls(
            scenario_id=settings.scenario_id,
            memory_types=settings.scenario_memory_types,
            business_progress_values=settings.scenario_business_progress_values,
            capture_guidance=settings.scenario_capture_guidance,
            policy_version=settings.scenario_policy_version,
        )
