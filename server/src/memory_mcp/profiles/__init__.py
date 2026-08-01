"""正式记忆配置；业务词义与 Memory Core 分离。"""

from memory_mcp.profiles.general_work import GeneralWorkProfile
from memory_mcp.profiles.investment_research import InvestmentResearchProfile


def built_in_profiles() -> tuple[GeneralWorkProfile, InvestmentResearchProfile]:
    """为生产组合根构造全部内置 Profile；公开默认仍由工具合同决定。"""

    return (GeneralWorkProfile(), InvestmentResearchProfile())


__all__ = [
    "GeneralWorkProfile",
    "InvestmentResearchProfile",
    "built_in_profiles",
]
