"""封装 Agent 调用、会话状态和响应解析。"""

from collections.abc import Sequence
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from agent_lab.exceptions import AgentExecutionError

from .schemas import AgentResponse, SourceCitation, TokenUsage


class AgentService:
    """按会话线程调用 LangChain Agent 的应用服务。"""

    def __init__(
        self,
        graph: CompiledStateGraph,
        checkpointer: BaseCheckpointSaver,
        *,
        recursion_limit: int,
    ) -> None:
        self._graph = graph
        self._checkpointer = checkpointer
        self._recursion_limit = recursion_limit

    def run(self, query: str, *, thread_id: str) -> AgentResponse:
        """执行一轮对话，并返回答案、来源和 Token 用量。"""

        if not query.strip():
            raise ValueError("query must not be empty")
        if not thread_id.strip():
            raise ValueError("thread_id must not be empty")

        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self._recursion_limit,
        }
        state = self._graph.invoke(
            {"messages": [HumanMessage(content=query)]},
            config=config,
        )
        messages = state.get("messages")
        if not isinstance(messages, Sequence):
            raise AgentExecutionError("Agent returned no message history.")

        current_turn = _current_turn_messages(messages)
        final_message = next(
            (
                message
                for message in reversed(current_turn)
                if isinstance(message, AIMessage) and message.text
            ),
            None,
        )
        if final_message is None:
            raise AgentExecutionError("Agent returned no final answer.")

        return AgentResponse(
            answer=final_message.text,
            sources=_extract_sources(current_turn),
            token_usage=_aggregate_token_usage(current_turn),
        )

    def clear_thread(self, thread_id: str) -> None:
        """删除指定线程在当前进程中的会话状态。"""

        self._checkpointer.delete_thread(thread_id)


def _current_turn_messages(messages: Sequence[Any]) -> list[BaseMessage]:
    """截取最后一条用户消息开始的本轮消息。"""

    typed_messages = [
        message for message in messages if isinstance(message, BaseMessage)
    ]
    last_human_index = next(
        (
            index
            for index in range(len(typed_messages) - 1, -1, -1)
            if isinstance(typed_messages[index], HumanMessage)
        ),
        0,
    )
    return typed_messages[last_human_index:]


def _extract_sources(messages: Sequence[BaseMessage]) -> tuple[SourceCitation, ...]:
    """从检索工具返回的文档中提取并去重引用来源。"""

    citations: dict[tuple[str, int | None, str | None], SourceCitation] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        artifact = message.artifact
        if not isinstance(artifact, list):
            continue
        for item in artifact:
            if not isinstance(item, Document):
                continue
            source = str(item.metadata.get("source", "unknown"))
            raw_page = item.metadata.get("page")
            page = _optional_int(raw_page)
            raw_chunk_id = item.metadata.get("chunk_id")
            chunk_id = str(raw_chunk_id) if raw_chunk_id else None
            key = (source, page, chunk_id)
            citations[key] = SourceCitation(
                source=source,
                page=page,
                chunk_id=chunk_id,
            )
    return tuple(citations.values())


def _optional_int(value: Any) -> int | None:
    """将可选值安全转换为整数。"""

    if not isinstance(value, int | str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _aggregate_token_usage(messages: Sequence[BaseMessage]) -> TokenUsage:
    """汇总本轮所有模型消息记录的 Token 用量。"""

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    for message in messages:
        if not isinstance(message, AIMessage) or not message.usage_metadata:
            continue
        input_tokens += message.usage_metadata.get("input_tokens", 0)
        output_tokens += message.usage_metadata.get("output_tokens", 0)
        total_tokens += message.usage_metadata.get("total_tokens", 0)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )
