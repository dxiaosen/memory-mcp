"""创建并装配 LangChain Agent。"""

from collections.abc import Sequence

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from .prompts import KNOWLEDGE_AGENT_SYSTEM_PROMPT
from .service import AgentService


def create_agent_service(
    chat_model: BaseChatModel,
    knowledge_search_tool: BaseTool,
    *,
    recursion_limit: int,
    additional_tools: Sequence[BaseTool] = (),
) -> AgentService:
    """创建默认知识库 Agent，并装配进程内会话状态。"""

    checkpointer = InMemorySaver()
    graph = create_agent(
        model=chat_model,
        tools=[
            knowledge_search_tool,
            *additional_tools,
        ],
        system_prompt=KNOWLEDGE_AGENT_SYSTEM_PROMPT,
        checkpointer=checkpointer,
        name="knowledge_agent",
    )
    return AgentService(
        graph=graph,
        checkpointer=checkpointer,
        recursion_limit=recursion_limit,
    )
