"""实现基于 Chroma 的持久化知识库适配器。"""

from collections.abc import Sequence
from pathlib import Path

from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

from agent_lab.config import Settings


class ChromaKnowledgeStore:
    """对外提供知识库操作的 Chroma 持久化适配器。"""

    def __init__(self, vector_store: Chroma) -> None:
        self._vector_store = vector_store

    def replace_documents(
        self,
        documents: Sequence[Document],
        *,
        ids: Sequence[str],
        document_ids: Sequence[str],
    ) -> list[str]:
        """替换指定源文档的全部分块，保证重复索引不产生副本。"""

        for document_id in set(document_ids):
            self._vector_store.delete(where={"document_id": document_id})
        return self._vector_store.add_documents(
            documents=list(documents),
            ids=list(ids),
        )

    def clear(self) -> None:
        """清空当前 Chroma 集合。"""

        self._vector_store.reset_collection()

    def as_retriever(self, *, top_k: int) -> BaseRetriever:
        """创建返回前 ``top_k`` 个结果的检索器。"""

        return self._vector_store.as_retriever(search_kwargs={"k": top_k})

    def count(self) -> int:
        """返回当前集合中的文本分块数量。"""

        result = self._vector_store.get(include=[])
        return len(result["ids"])


def create_knowledge_store(
    settings: Settings,
    embedding_model: Embeddings,
) -> ChromaKnowledgeStore:
    """创建配置指定的持久化 Chroma 知识库。"""

    persist_directory = _resolve_persist_directory(
        settings.vector_store_persist_directory
    )
    vector_store = Chroma(
        collection_name=settings.vector_store_collection_name,
        embedding_function=embedding_model,
        persist_directory=str(persist_directory),
        client_settings=ChromaSettings(anonymized_telemetry=False),
    )
    return ChromaKnowledgeStore(vector_store)


def _resolve_persist_directory(path: Path) -> Path:
    """解析并创建向量数据库的持久化目录。"""

    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved
