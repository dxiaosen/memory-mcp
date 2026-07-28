"""定义索引与检索依赖的知识库存储协议。"""

from collections.abc import Sequence
from typing import Protocol

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever


class KnowledgeStore(Protocol):
    """索引和检索流程所需的最小存储接口。"""

    def replace_documents(
        self,
        documents: Sequence[Document],
        *,
        ids: Sequence[str],
        document_ids: Sequence[str],
    ) -> list[str]:
        """替换指定源文档对应的全部文本分块。"""

        ...

    def clear(self) -> None:
        """清空知识库。"""

        ...

    def as_retriever(self, *, top_k: int) -> BaseRetriever:
        """创建指定召回数量的 LangChain 检索器。"""

        ...

    def count(self) -> int:
        """返回知识库中的文本分块数量。"""

        ...
