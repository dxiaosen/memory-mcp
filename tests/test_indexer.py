from collections.abc import Sequence

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from agent_lab.knowledge import KnowledgeIndexer


class StaticLoader:
    def load(self, paths: Sequence[str]) -> list[Document]:
        return [
            Document(
                page_content="第一条制度内容。第二条制度内容。",
                metadata={
                    "document_id": "document-1",
                    "source": "policy.md",
                    "source_path": "policy.md",
                    "file_type": "md",
                    "page": 1,
                },
            )
        ]


class RecordingStore:
    def __init__(self) -> None:
        self.cleared = False
        self.documents: list[Document] = []
        self.ids: list[str] = []

    def replace_documents(
        self,
        documents: Sequence[Document],
        *,
        ids: Sequence[str],
        document_ids: Sequence[str],
    ) -> list[str]:
        self.documents = list(documents)
        self.ids = list(ids)
        assert set(document_ids) == {"document-1"}
        return self.ids

    def clear(self) -> None:
        self.cleared = True

    def as_retriever(self, *, top_k: int) -> BaseRetriever:
        raise NotImplementedError

    def count(self) -> int:
        return len(self.documents)


def test_indexer_splits_documents_and_assigns_stable_ids() -> None:
    store = RecordingStore()
    indexer = KnowledgeIndexer(
        store,
        chunk_size=12,
        chunk_overlap=2,
        loader=StaticLoader(),
    )

    first_report = indexer.index(["unused"], rebuild=True)
    first_ids = list(store.ids)
    indexer.index(["unused"])

    assert first_report.rebuilt is True
    assert first_report.source_file_count == 1
    assert first_report.chunk_count >= 2
    assert store.cleared is True
    assert store.ids == first_ids
    assert all(document.metadata["chunk_id"] for document in store.documents)
