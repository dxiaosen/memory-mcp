"""将本地知识文档切分并写入向量存储。"""

import hashlib
from collections.abc import Sequence
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from agent_lab.exceptions import KnowledgeBaseError

from .loaders import KnowledgeDocumentLoader
from .ports import KnowledgeStore
from .schemas import IndexingReport

_CJK_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    "；",
    "，",
    ".",
    "!",
    "?",
    ";",
    ",",
    " ",
    "",
]


class KnowledgeIndexer:
    """加载、切分文档，并以幂等方式写入知识库。"""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        chunk_size: int,
        chunk_overlap: int,
        loader: KnowledgeDocumentLoader | None = None,
    ) -> None:
        self._store = store
        self._loader = loader or KnowledgeDocumentLoader()
        self._splitter = RecursiveCharacterTextSplitter(
            separators=_CJK_SEPARATORS,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            add_start_index=True,
        )

    def index(
        self,
        paths: Sequence[str | Path],
        *,
        rebuild: bool = False,
    ) -> IndexingReport:
        """索引指定文件或目录，并返回本次索引报告。"""

        source_documents = self._loader.load(paths)
        chunks = [
            chunk
            for chunk in self._splitter.split_documents(source_documents)
            if chunk.page_content.strip()
        ]
        if not chunks:
            raise KnowledgeBaseError("Knowledge files contain no indexable text.")

        indexed_chunks, chunk_ids = self._prepare_chunks(chunks)
        if rebuild:
            self._store.clear()

        document_ids = [str(chunk.metadata["document_id"]) for chunk in indexed_chunks]
        self._store.replace_documents(
            indexed_chunks,
            ids=chunk_ids,
            document_ids=document_ids,
        )
        source_files = {
            str(document.metadata["source_path"]) for document in source_documents
        }
        return IndexingReport(
            source_file_count=len(source_files),
            source_document_count=len(source_documents),
            chunk_count=len(indexed_chunks),
            stored_chunk_count=self._store.count(),
            rebuilt=rebuild,
        )

    def _prepare_chunks(
        self,
        chunks: Sequence[Document],
    ) -> tuple[list[Document], list[str]]:
        """为文本分块补充稳定 ID 及索引元数据。"""

        indexed_chunks: list[Document] = []
        chunk_ids: list[str] = []
        for chunk in chunks:
            chunk_id = self._chunk_id(chunk)
            metadata = {**chunk.metadata, "chunk_id": chunk_id}
            indexed_chunks.append(
                Document(
                    id=chunk_id,
                    page_content=chunk.page_content,
                    metadata=metadata,
                )
            )
            chunk_ids.append(chunk_id)
        return indexed_chunks, chunk_ids

    def _chunk_id(self, chunk: Document) -> str:
        """根据文档位置和内容计算稳定的分块 ID。"""

        identity = "\x1f".join(
            [
                str(chunk.metadata["document_id"]),
                str(chunk.metadata["page"]),
                str(chunk.metadata.get("start_index", 0)),
                chunk.page_content,
            ]
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
