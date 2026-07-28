"""定义知识库索引流程的结果模型。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IndexingReport:
    """单次知识库索引完成后的结果摘要。"""

    source_file_count: int
    source_document_count: int
    chunk_count: int
    stored_chunk_count: int
    rebuilt: bool
