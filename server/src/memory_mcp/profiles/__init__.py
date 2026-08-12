"""正式记忆配置；业务词义与 Memory Core 分离。"""

from memory_mcp.core import (
    InvalidMemoryProfileError,
    MemoryProfile,
    profile_fingerprint,
)
from memory_mcp.profiles.general_work import GeneralWorkProfile
from memory_mcp.profiles.investment_research import InvestmentResearchProfile

_BUILT_IN_POLICY_FINGERPRINTS = {
    ("general-work", "v1"): (
        "059cc7aec27384deae273124ed1fae862bd275fca29dc7a65e16eb2b744b69ac"
    ),
    ("investment-research", "v1"): (
        "ed1af663f7769fc16b309945b951d5686d44511e3517cc22a5ce82f444847213"
    ),
}


def built_in_profiles() -> tuple[GeneralWorkProfile, InvestmentResearchProfile]:
    """构造并校验全部内置 Profile，防止版本不变时策略静默漂移。"""

    profiles = (GeneralWorkProfile(), InvestmentResearchProfile())
    for profile in profiles:
        validate_built_in_profile(profile)
    return profiles


def validate_built_in_profile(profile: MemoryProfile) -> None:
    """校验内置 Profile 的策略指纹与已注册的固定值一致，防止版本不变时策略漂移。"""

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
