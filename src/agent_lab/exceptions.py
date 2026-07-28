"""定义应用可预期的异常类型。"""


class AgentLabError(Exception):
    """应用可预期异常的基类。"""


class ConfigurationError(AgentLabError):
    """配置不完整或不受支持时抛出。"""


class KnowledgeBaseError(AgentLabError):
    """知识文档无法加载、索引或检索时抛出。"""


class AgentExecutionError(AgentLabError):
    """Agent 未生成有效结果时抛出。"""
