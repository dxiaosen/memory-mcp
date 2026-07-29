"""应用依赖装配入口。"""

import logging

from agent_lab.agents import AgentService, create_agent_service
from agent_lab.config import AgentSettings, KnowledgeSettings
from agent_lab.integrations import (
    create_chat_model,
    create_embedding_model,
    create_knowledge_store,
)
from agent_lab.knowledge import (
    KnowledgeIndexer,
    create_knowledge_search_tool,
)
from agent_lab.observability import log_event

_LOGGER = logging.getLogger(__name__)


def build_knowledge_indexer(settings: KnowledgeSettings) -> KnowledgeIndexer:
    """装配离线知识库索引流程。"""

    log_event(
        _LOGGER,
        logging.DEBUG,
        "bootstrap.knowledge_indexer.started",
        embedding_provider=settings.embedding_model_provider,
    )
    embedding_model = create_embedding_model(settings)
    store = create_knowledge_store(settings, embedding_model)
    indexer = KnowledgeIndexer(
        store,
        chunk_size=settings.document_chunk_size,
        chunk_overlap=settings.document_chunk_overlap,
    )
    log_event(
        _LOGGER,
        logging.DEBUG,
        "bootstrap.knowledge_indexer.completed",
        chunk_overlap=settings.document_chunk_overlap,
        chunk_size=settings.document_chunk_size,
    )
    return indexer


def build_agent_service(settings: AgentSettings) -> AgentService:
    """装配在线 RAG Agent 问答流程。"""

    log_event(
        _LOGGER,
        logging.DEBUG,
        "bootstrap.agent_service.started",
        chat_provider=settings.chat_model_provider,
        embedding_provider=settings.embedding_model_provider,
    )
    chat_model = create_chat_model(settings)
    embedding_model = create_embedding_model(settings)
    store = create_knowledge_store(settings, embedding_model)
    knowledge_search_tool = create_knowledge_search_tool(
        store,
        top_k=settings.retrieval_top_k,
    )
    service = create_agent_service(
        chat_model,
        knowledge_search_tool,
        recursion_limit=settings.agent_recursion_limit,
    )
    log_event(
        _LOGGER,
        logging.DEBUG,
        "bootstrap.agent_service.completed",
        recursion_limit=settings.agent_recursion_limit,
        retrieval_top_k=settings.retrieval_top_k,
    )
    return service
