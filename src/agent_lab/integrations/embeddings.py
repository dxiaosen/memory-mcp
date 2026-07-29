"""根据配置创建文本向量化模型。"""

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from agent_lab.config import KnowledgeSettings
from agent_lab.exceptions import ConfigurationError


def create_embedding_model(settings: KnowledgeSettings) -> Embeddings:
    """创建配置指定的 LangChain Embedding 模型。"""

    if settings.embedding_model_provider == "openai":
        return OpenAIEmbeddings(
            model=settings.embedding_model_name,
            api_key=settings.embedding_model_api_key,
            base_url=settings.embedding_model_base_url,
            timeout=settings.embedding_model_timeout_seconds,
            max_retries=settings.embedding_model_max_retries,
            check_embedding_ctx_length=False,
        )

    raise ConfigurationError(
        f"Unsupported embedding model provider: {settings.embedding_model_provider}"
    )
