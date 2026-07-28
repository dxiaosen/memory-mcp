from pathlib import Path

import pytest

from agent_lab.exceptions import KnowledgeBaseError
from agent_lab.knowledge import KnowledgeDocumentLoader

_FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures" / "knowledge"


def test_loader_returns_langchain_documents_with_source_metadata() -> None:
    documents = KnowledgeDocumentLoader().load([_FIXTURE_DIRECTORY])

    assert len(documents) == 1
    assert documents[0].page_content.strip() == "项目知识"
    assert documents[0].metadata["source"] == "notes.md"
    assert documents[0].metadata["page"] == 1
    assert documents[0].metadata["document_id"]


def test_loader_rejects_unsupported_explicit_file() -> None:
    unsupported = _FIXTURE_DIRECTORY / "ignored.json"

    with pytest.raises(KnowledgeBaseError, match="Unsupported"):
        KnowledgeDocumentLoader().load([unsupported])
