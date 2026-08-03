## 1. OpenSpec 变更提案

- [x] 1.1 创建 `openspec/changes/split-evidence-document-metadata/` 及 spec delta。
- [x] 1.2 `openspec-cn validate split-evidence-document-metadata --strict` 通过。

## 2. DB schema 拆分

- [x] 2.1 修改 `0001_memory_schema.sql`：evidence 主表去掉 7 个文档列，放宽 conversation_id NOT NULL；新增 `memory_evidence_documents` 子表。
- [x] 2.2 review_items 同步拆分：去掉 7 个文档列，新增 `memory_review_item_documents` 子表。
- [x] 2.3 新增 `0003_split_evidence_documents.sql`：对已有库执行拆表迁移（DO 块处理列不存在，含 review_items）。
- [x] 2.4 开发库重建 schema，validate_schema 通过。

## 3. domain model 更新

- [x] 3.1 `Evidence` 去掉 7 个文档字段，`conversation_id` 改可选。
- [x] 3.2 新增 `EvidenceDocument` dataclass。
- [x] 3.3 `RecallSourceSummary` 去掉内联文档字段，加可选 `document`。
- [x] 3.4 导出新类型到 `core.domain.__init__` 和 `core.__init__`。

## 4. adapters 更新

- [x] 4.1 PostgreSQL `mapping.py`：`to_evidence`/`load_evidence` LEFT JOIN 子表，新增 `_to_evidence_document`。
- [x] 4.2 PostgreSQL `repository.py`：`_insert_evidence` 主表去文档列，文档来源写子表。
- [x] 4.3 PostgreSQL `repository.py`：`_insert_review` 主表去文档列，文档来源写子表；`_SELECT_REVIEW` LEFT JOIN 子表。
- [x] 4.4 PostgreSQL `recall.py`：`load_recall_evidence` LEFT JOIN 子表。
- [x] 4.5 PostgreSQL `validation.py`：`validate_review_memory` 改用 `_evidence_document_mismatch`。
- [x] 4.6 in_memory：`_validate_new_evidence` 改用 `_evidence_document_mismatch`。

## 5. schemas 和召回渲染更新

- [x] 5.1 `EvidenceView` 和 `RecallSourceView` 去内联文档字段，加可选 `document` 子对象（`EvidenceDocumentView`）。
- [x] 5.2 `MemoryHistoryEntryView.from_entry` 和 `RecallReceipt.from_result` 适配。
- [x] 5.3 `recall_service._source_summaries` 适配。
- [x] 5.4 `candidate_processing._evidence` 从 candidate 内联字段构造 `EvidenceDocument`。

## 6. 测试

- [x] 6.1 更新 evidence 相关测试断言（`source.document.*`）。
- [x] 6.2 跑全量 pytest 通过（186 passed）。
- [x] 6.3 真实 DB 契约测试通过。

## 7. 文档

- [x] 7.1 design.md 更新 evidence 表结构和来源类型说明。

## 8. 验证

- [x] 8.1 ruff format + check 通过。
- [x] 8.2 pytest + evals + openspec validate 通过。
