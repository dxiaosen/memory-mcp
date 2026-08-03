## Context

`memory_evidence` 当前混存两类来源元数据：对话来源（`conversation_id`/`source_turn_id` 必填）和文档/网页引用（7 个可选字段全空）。投研场景引用研报/公告是高频需求，混表会在数据增长后造成稀疏列膨胀、语义混乱（文档来源被强制填 `conversation_id`）和扩展困难。当前 evidence 表为空，拆表零迁移成本。

约束：不改 capture 热路径（Agent 当前不传文档字段）；不改 MCP 工具签名；PostgreSQL 是唯一权威；core.domain 不依赖 DB/MCP；离线评测确定性可复现。

## Goals / Non-Goals

**Goals:**

- `memory_evidence` 主表只保留通用必填字段，不再带 7 个文档可选列。
- 新增 `memory_evidence_documents` 子表，仅 document/web 来源有行。
- domain model 和召回渲染适配拆表结构。
- 文档来源不再被强制填 `conversation_id`。

**Non-Goals:**

- 不改 capture 热路径（Agent 当前不传文档字段，拆表后子表无行，行为不变）。
- 不引入外部文档存储或全文索引。
- 不改 MCP 工具签名。
- 不做文档来源的 capture 接入（留作后续投研 Agent 接入文档引用时）。

## Decisions

### 1. 拆表结构

`memory_evidence` 主表去掉 `source_uri`/`source_title`/`source_publisher`/`published_at`/`retrieved_at`/`content_hash`/`citation_locator`。保留 `source_type` 作为来源类型标记（conversation/tool/document/web）。`conversation_id` 对 document/web 来源改为可选（去掉 NOT NULL）——文档来源可以没有会话上下文。

`memory_evidence_documents` 子表：`evidence_id UUID PRIMARY KEY`（1:1 到 evidence），存放 7 个文档字段。只有 `source_type IN ('document','web')` 时才有行。子表无 owner_id（它不是独立隔离单元，跟随主表 evidence 的 owner）。

**Rejected alternatives:**
- 保留混表只删文档字段：不解决"文档来源需要 conversation_id"的语义问题，且未来加回要两次 migration。
- 用 JSON 列存文档元数据：破坏约束校验和索引能力。
- 按 source_type 多表继承（conversation 一张、document 一张）：表数膨胀，查询要 UNION ALL，复杂度过高。

### 2. domain model 拆分

`Evidence` 去掉 7 个文档字段。新增 `EvidenceDocument` dataclass，存放文档元数据。`MemoryRecord.evidence` 仍是 `tuple[Evidence, ...]`，文档元数据不内联进 Evidence（保持通用来源的干净）。召回时 `RecallSourceSummary` 带可选 `document: EvidenceDocument | None`。

**Rejected alternatives:**
- Evidence 内嵌可选 EvidenceDocument：仍是"一个类两种形态"，没解决稀疏问题。
- 用协议/联合类型区分来源：过度设计，当前两类来源字段差异明确。

### 3. 召回渲染

`load_recall_evidence` LEFT JOIN `memory_evidence_documents`，文档来源的行带文档元数据，对话来源的 document 为 None。`RecallSourceSummary` 和 `RecallSourceView` 去掉内联的 7 个文档字段，改为可选 `document` 子对象。渲染时文档来源才输出文档信息。

## Dependency Direction

- `core.domain.Evidence` 和 `EvidenceDocument` 不依赖 DB/MCP。
- PostgreSQL mapping 和 recall 用 LEFT JOIN 子表。
- in_memory adapter 用嵌套结构模拟。
- schemas 的 `RecallSourceView` 暴露可选 `document` 子对象。
- capture 热路径不改：Agent 不传文档字段时，子表无行，Evidence 无 document。

## Risks / Trade-offs

- [拆表增加一次 JOIN] → 召回已有 LEFT JOIN（evidence 本就批量加载），多 JOIN 子表成本极低；且文档来源当前为空，无性能影响。
- [conversation_id 改可选可能影响现有对话来源] → 对话来源仍填 conversation_id（capture 路径不变），只放宽 NOT NULL 约束，不破坏现有数据。
- [Agent 不传文档字段时子表空] → 这是预期行为；投研 Agent 接入文档引用前，拆表只是结构优化，无功能变化。

## Migration Plan

1. 新增 migration `0003_split_evidence_documents.sql`：从 `memory_evidence` 复制文档字段到新子表，删除主表的文档列，放宽 conversation_id NOT NULL。
2. 开发库重建 schema（drop + 跑全部 migration）。
3. 发布 Server 代码；Agent 不受影响。
4. 跑离线评测确认 `recall_at_k` 不回退。
5. 回滚：migration 不可逆（删列后需前向加回）；代码回滚后 evidence 退化为主表无文档字段。
