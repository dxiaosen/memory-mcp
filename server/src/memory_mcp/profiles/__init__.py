"""正式记忆配置；业务词义与 Memory Core 分离。"""

from memory_mcp.core import (
    InvalidMemoryProfileError,
    MemoryProfile,
    profile_fingerprint,
)
from memory_mcp.profiles.general_work import GeneralWorkProfile
from memory_mcp.profiles.investment_research import InvestmentResearchProfile

_BUILT_IN_POLICY_FINGERPRINTS = {
    ("general-work", "general-work-v2"): (
        "3daa04b3e3aa75ed0ce128fd35138976369001f88f1af5e4042dbb2dc13a4983"
    ),
    ("investment-research", "investment-research-v2"): (
        "808ff56faa8720991e48b622f4041c6c987a3c639498e6a17b7a525f8896ca88"
    ),
}


def built_in_profiles() -> tuple[GeneralWorkProfile, InvestmentResearchProfile]:
    """构造并校验全部内置 Profile，防止版本不变时策略静默漂移。"""

    profiles = (GeneralWorkProfile(), InvestmentResearchProfile())
    for profile in profiles:
        validate_built_in_profile(profile)
    return profiles


def validate_built_in_profile(profile: MemoryProfile) -> None:
    """校验内置 Profile 的版本已明确绑定当前策略内容。"""

    key = (profile.profile_id, profile.profile_version)
    expected = _BUILT_IN_POLICY_FINGERPRINTS.get(key)
    actual = profile_fingerprint(profile)
    if expected is None or actual != expected:
        raise InvalidMemoryProfileError(
            "built-in profile policy fingerprint is not registered for "
            f"{profile.profile_id} {profile.profile_version}"
        )


__all__ = [
    "GeneralWorkProfile",
    "InvestmentResearchProfile",
    "built_in_profiles",
    "validate_built_in_profile",
]
