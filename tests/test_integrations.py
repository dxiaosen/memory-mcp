from uuid import uuid4

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.messages import ToolMessage
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from agent_lab.config import Settings
from agent_lab.integrations import (
    ChromaKnowledgeStore,
    create_chat_model,
    create_embedding_model,
)
from agent_lab.knowledge import create_knowledge_search_tool


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "chat_model_name": "chat-model",
        "chat_model_api_key": "chat-key",
        "embedding_model_name": "embedding-model",
        "embedding_model_api_key": "embedding-key",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_chat_model_factory_uses_provider_specific_integrations() -> None:
    deepseek_model = create_chat_model(_settings(chat_model_provider="deepseek"))
    openai_model = create_chat_model(_settings(chat_model_provider="openai"))

    assert isinstance(deepseek_model, ChatDeepSeek)
    assert isinstance(openai_model, ChatOpenAI)


def test_embedding_factory_returns_langchain_embedding_model() -> None:
    embedding_model = create_embedding_model(_settings())

    assert isinstance(embedding_model, OpenAIEmbeddings)
    assert embedding_model.model == "embedding-model"


def test_chroma_store_replaces_all_chunks_for_a_document() -> None:
    vector_store = Chroma(
        collection_name=f"test-{uuid4().hex}",
        embedding_function=DeterministicFakeEmbedding(size=32),
    )
    store = ChromaKnowledgeStore(vector_store)
    first = Document(
        page_content="旧内容",
        metadata={"document_id": "doc-1", "source": "test.md", "page": 1},
    )
    second = Document(
        page_content="新内容",
        metadata={"document_id": "doc-1", "source": "test.md", "page": 1},
    )

    store.replace_documents([first], ids=["chunk-old"], document_ids=["doc-1"])
    store.replace_documents([second], ids=["chunk-new"], document_ids=["doc-1"])

    assert store.count() == 1
    results = store.as_retriever(top_k=1).invoke("新内容")
    assert results[0].page_content == "新内容"

    retrieval_tool = create_knowledge_search_tool(store, top_k=1)
    tool_message = retrieval_tool.invoke(
        {
            "name": "search_knowledge_base",
            "args": {"query": "新内容"},
            "id": "call-1",
            "type": "tool_call",
        }
    )
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.artifact[0].page_content == "新内容"
