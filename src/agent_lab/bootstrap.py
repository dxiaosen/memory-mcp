"""应用依赖装配入口。"""

from agent_lab.agents import AgentService, create_agent_service
from agent_lab.config import Settings
from agent_lab.integrations import (
    create_chat_model,
    create_embedding_model,
    create_knowledge_store,
)
from agent_lab.knowledge import (
    KnowledgeIndexer,
    create_knowledge_search_tool,
)


def build_knowledge_indexer(settings: Settings) -> KnowledgeIndexer:
    """装配离线知识库索引流程。"""

    embedding_model = create_embedding_model(settings)
    store = create_knowledge_store(settings, embedding_model)
    return KnowledgeIndexer(
        store,
        chunk_size=settings.document_chunk_size,
        chunk_overlap=settings.document_chunk_overlap,
    )


def build_agent_service(settings: Settings) -> AgentService:
    """装配在线 RAG Agent 问答流程。"""

    chat_model = create_chat_model(settings)
    embedding_model = create_embedding_model(settings)
    store = create_knowledge_store(settings, embedding_model)
    knowledge_search_tool = create_knowledge_search_tool(
        store,
        top_k=settings.retrieval_top_k,
    )
    return create_agent_service(
        chat_model,
        knowledge_search_tool,
        recursion_limit=settings.agent_recursion_limit,
    )
