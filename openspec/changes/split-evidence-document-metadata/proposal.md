## Why

当前 `memory_evidence` 表把"对话来源"和"文档/网页引用"两类语义不同的元数据混在一张表里。对话证据的必填字段（`conversation_id`、`source_turn_id`）对文档来源没有意义，而文档引用的 7 个可选字段（`source_uri`、`source_title`、`source_publisher`、`published_at`、`retrieved_at`、`content_hash`、`citation_locator`）在对话场景下永远是 NULL，形成稀疏列。投研场景引用研报/公告/新闻是高频需求，当前 evidence 表已为文档来源预留了字段，但混表设计会在数据增长后造成语义混乱、查询浪费和扩展困难。

## What Changes

- 将 `memory_evidence` 表的文档/网页引用字段拆到独立子表 `memory_evidence_documents`。
- `memory_evidence` 主表只保留所有来源通用的必填字段（`evidence_id`、`memory_id`、`revision_id`、`owner_id`、`source_type`、`source_role`、`source_message_id`、`source_tool_name`、`conversation_id`、`source_turn_id`、`source_expression`、`observed_at`、`created_at`）。
- `memory_evidence_documents` 子表只有 `source_type IN ('document','web')` 时才有行，存放 `source_uri`、`source_title`、`source_publisher`、`published_at`、`retrieved_at`、`content_hash`、`citation_locator`。
- `Evidence` domain model 拆为 `Evidence`（通用）+ 可选 `EvidenceDocument`（文档元数据）。
- 召回的 `RecallSourceSummary` 和 `RecallSourceView` 去掉内联的文档字段，改为可选的 `document` 子对象。
- 对话来源的 `conversation_id` 对文档来源不再强制 NOT NULL（语义上文档来源可以没有会话上下文）。

## Capabilities

### New Capabilities

- `evidence-source-split`: 规定 evidence 通用主表与文档引用子表的分离、来源类型的字段归属、以及召回渲染的文档元数据可选性。

### Modified Capabilities

无。主规范尚未归档，本变更以独立增量能力描述新增行为。

## Impact

- DB：新增 migration `0003_split_evidence_documents.sql`，拆 `memory_evidence` 列到新子表；不删历史数据（当前为空）。
- Core：`Evidence` domain model 去文档字段，新增 `EvidenceDocument`；`RecallSourceSummary` 改造。
- PostgreSQL：`mapping.py` 的 `load_evidence`/`to_evidence` 改造；`repository.py` 的 `_insert_evidence` 改造；`recall.py` 的 `load_recall_evidence` LEFT JOIN 子表。
- in_memory：同步改造 evidence 存储和读取。
- schemas：`RecallSourceView` 去内联文档字段，加可选 `document` 子对象。
- 非目标：不改 capture 热路径（Agent 当前不传文档字段，子表行为不变）；不引入外部文档存储；不改 MCP 工具签名。
