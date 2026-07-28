from typing import Any

from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool

from agent_lab.agents import AgentService, create_agent_service


class FakeGraph:
    def invoke(self, state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        assert config["configurable"]["thread_id"] == "thread-1"
        assert config["recursion_limit"] == 8
        return {
            "messages": [
                *state["messages"],
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_knowledge_base",
                            "args": {"query": "制度"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    },
                ),
                ToolMessage(
                    content="制度内容",
                    tool_call_id="call-1",
                    name="search_knowledge_base",
                    artifact=[
                        Document(
                            page_content="制度内容",
                            metadata={
                                "source": "policy.md",
                                "page": 2,
                                "chunk_id": "chunk-1",
                            },
                        )
                    ],
                ),
                AIMessage(
                    content="制度要求如下。[policy.md，第 2 页]",
                    usage_metadata={
                        "input_tokens": 20,
                        "output_tokens": 10,
                        "total_tokens": 30,
                    },
                ),
            ]
        }


class FakeCheckpointer:
    def __init__(self) -> None:
        self.deleted_thread: str | None = None

    def delete_thread(self, thread_id: str) -> None:
        self.deleted_thread = thread_id


class ToolCapableFakeChatModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: list[BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> ToolCapableFakeChatModel:
        return self


def test_agent_service_returns_typed_answer_sources_and_usage() -> None:
    checkpointer = FakeCheckpointer()
    service = AgentService(
        graph=FakeGraph(),
        checkpointer=checkpointer,
        recursion_limit=8,
    )

    response = service.run("制度是什么？", thread_id="thread-1")
    service.clear_thread("thread-1")

    assert response.answer == "制度要求如下。[policy.md，第 2 页]"
    assert response.sources[0].source == "policy.md"
    assert response.sources[0].page == 2
    assert response.token_usage.input_tokens == 30
    assert response.token_usage.output_tokens == 15
    assert response.token_usage.total_tokens == 45
    assert checkpointer.deleted_thread == "thread-1"


def test_agent_service_rejects_empty_query() -> None:
    service = AgentService(
        graph=FakeGraph(),
        checkpointer=FakeCheckpointer(),
        recursion_limit=8,
    )

    try:
        service.run(" ", thread_id="thread-1")
    except ValueError as exc:
        assert str(exc) == "query must not be empty"
    else:
        raise AssertionError("Expected ValueError")


def test_agent_factory_builds_a_runnable_langchain_agent() -> None:
    model = ToolCapableFakeChatModel(messages=iter(["测试回答"]))
    retrieval_tool = StructuredTool.from_function(
        func=lambda query: "测试资料",
        name="search_knowledge_base",
        description="检索测试知识库",
    )
    service = create_agent_service(
        model,
        retrieval_tool,
        recursion_limit=8,
    )

    response = service.run("你好", thread_id="factory-test")

    assert response.answer == "测试回答"
