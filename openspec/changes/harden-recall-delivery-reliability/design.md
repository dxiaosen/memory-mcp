## Context

当前 Server 的召回候选已经在 PostgreSQL 内完成 owner/Profile/effective 过滤和 trigram/recent 限制，但 `to_record()` 会逐候选执行 Evidence 查询，默认 500 候选时形成 N+1。RecallService 的排序和 token 裁剪并不依赖 Evidence，只有最终结构化响应需要最近三条来源，因此可以延迟水合。

Agent command Hook 是短生命周期进程。BeforeRun 已用 0600 JSON 保存 prompt，Stop 收到 final output 后同步调用 `capture_completed_turn`。服务端可能以成功 MCP 响应返回 `failed` 或 `reprocess_required`；当前 Bridge 只重试 HTTP/工具异常，Host adapter 又在 `finally` 中删除状态，所以可重处理 payload 会静默丢失。服务端应用锁只覆盖同一 `MemoryService`，PostgreSQL advisory lock 位于模型处理后的提交阶段，因此跨实例只能保证唯一数据库写入，不能保证最多一次模型调用。

维护 runner 与 MCP 共用 lifespan。读取谓词会立即排除到期数据，但 runner 失败只进入日志，health 无法显示物化维护是否长期滞后。

约束：保持 Agent 仅配置 URL/Token，不新增外部队列、常驻 daemon 或公开 owner 参数；业务正文不进入 operational log；PostgreSQL 继续是唯一权威存储；本次不把未经评测证明的模型调用放进 BeforeRun 热路径。

## Goals / Non-Goals

**Goals:**

- Recall 的数据库往返数不随候选数量线性增长，且最终只加载每个命中 revision 最近三条 Evidence。
- Agent 区分 `completed`、永久 `failed`、`reprocess_required` 和传输 warning，固定重放 payload 的 observed time，并在后续同项目 Stop 中有界重试可重处理项。
- 跨 Service 同 payload 重叠提交最多产生一份 memory/review/evidence/relation 写入；后提交者读取权威 receipt 并标记 replay。
- `/health` 公开无正文 maintenance 子状态，日志包含连续失败数与最近成功时间。
- 评测覆盖空召回、同义表达、强干扰和更大候选窗口；真实 PostgreSQL 合同执行新召回和维护 SQL。

**Non-Goals:**

- 外部消息队列、可靠无限期投递、常驻 Agent worker。
- 跨实例最多一次模型调用；本次只保证最多一次权威提交，并准确记录这一边界。
- Embedding、pgvector、LLM query expansion/rerank；只有新基准证明确定性召回不足后才单独提案。
- 自动事实核验、按使用频率改写内容、物理删除、suppression 或 OAuth/OIDC。

## Decisions

### 1. 使用无 Evidence 的专用召回候选

新增 `MemoryRecallCandidate(item, current_revision)` 和 `RecallCandidateSet.candidates`。Repository 的候选查询只映射 Item/Revision；RecallService 完成候选排序、关系加权、数量/token 选择后，通过新端口按 revision ID 批量取得最近三条 Evidence，再组装最终 `RecalledMemory`。

PostgreSQL 使用一个带 `row_number() over (partition by revision_id order by created_at desc, evidence_id desc)` 的查询限制每个 revision 三条，再按稳定升序返回。InMemory 从完整记录中切片，以保持合同等价。

拒绝仅把 N 次 Evidence 查询合并为一次但仍水合 500 候选：它减少往返，却仍产生无界内存和正文加载。拒绝让 `MemoryRecord.evidence` 为空：这会削弱完整记忆卡片的领域不变量。

### 2. 捕获提交返回数据库权威结果

`MemoryRepository.commit_capture()` 改为返回 `CaptureResult`。首次提交返回输入结果；若 advisory lock 后发现相同逻辑事件已经是终态且 fingerprint 相同，Repository 不执行候选写入，读取已有 outcomes 并返回 `replayed=true`；fingerprint 不同则抛稳定 `IdempotencyConflictError`。`CaptureService` 始终返回 Repository 结果。

两个实例仍可能在提交前各调用一次模型。为完全避免这一点需要持久 claim/lease/fencing 或异步 inbox，会显著改变同步 capture 合同。本次明确保证“最多一次提交”并修正文档，不在模型调用期间占用数据库连接或事务。

### 3. 复用现有 0600 状态文件形成有界本地 outbox

`TurnState` 向后兼容增加 `final_output`、`capture_observed_at` 和可选 Profile。Stop 在网络调用前用原子替换写入完整 payload；所有重试使用首次持久化的 aware timestamp，因此 event fingerprint 稳定。

状态处理规则：

- `completed`：删除；
- `failed`：删除并输出稳定永久失败 warning；
- `reprocess_required`：保留并输出 warning；
- 客户端/传输 warning：保留；
- 后续同项目 Stop 在处理当前轮次前最多重试一个旧 pending 项，失败不阻断当前业务轮次。

本地 outbox 继续使用 24 小时 TTL，属于崩溃可恢复的有界 best-effort，不宣称无限期可靠队列。后续若需要保证投递、削峰和死信，应改为 Server PostgreSQL inbox + worker，而不是扩展 command Hook 进程。

### 4. maintenance health 与业务可用性分离

Server 保存 `disabled|starting|ok|degraded`、last success、last failure、consecutive failures 和安全 error type。数据库不可用仍返回 503；单次维护失败不让 MCP 不可用，因为读取过滤仍正确，但 health JSON 将 maintenance 标为 degraded，便于监控告警。成功批次清零连续失败。

### 5. 评测先于模型召回

离线基准继续确定性执行，但新增至少一个零命中、一个同义改写、一个报告期/同名实体强干扰和一个百级候选案例。PostgreSQL 专用合同在显式测试数据库运行，验证结果、owner 隔离、Evidence 每 revision 上限和维护幂等。若同义改写失败，先记录失败并基于错误类型选择 FTS/同义词、Embedding 或可选 query expansion；不通过放宽阈值隐藏问题。

## Risks / Trade-offs

- [旧 pending 状态没有 final output] → 继续可读取并等待对应 Stop 补全；不猜测 transcript。
- [重试旧 outbox 增加 Stop 延迟] → 每次最多一个、仅在存在异常积压时发生，并记录耗时；正常路径不增加请求。
- [跨实例仍重复模型成本] → 文档不再声称最多一次抽取；数据库仍保证最多一次提交。多 worker 上线前另行实现 claim/fencing。
- [health 响应暴露异常类型] → 只记录类名和时间/计数，不记录异常 message、DSN、owner 或正文。
- [候选 DTO 增加一个领域类型] → 它明确区分“排序候选”和“完整 MemoryRecord”，避免用空 Evidence 破坏不变量。
- [语义案例可能揭示当前算法不足] → 失败是选型证据；不会用测试特例或模型硬编码伪造满分。

## Migration Plan

1. 先发布兼容的 Server/Agent 代码；本变更预期不需要数据库 migration。
2. 用专用 PostgreSQL 测试库执行 migration、混合召回、Evidence 批量水合、维护和双 Service 重叠提交合同。
3. Agent 新状态写 schema v2；旧 schema v1 文件无需离线迁移，新 Stop 首次补齐时原子升级。
4. 滚动发布 Server 后再升级 Agent。旧 Agent 仍兼容现有 MCP 工具；新 Agent 对旧 Server 的 completed/failed 字段保持兼容。
5. 若需回滚 Agent，先让 0.2 Client 投递或按 TTL 清理 v2 pending，再恢复 0.1 wheel；
   0.2 可以读取 v1，但 0.1 不承诺读取包含完整 payload 的 v2 文件。Server 可独立回滚。

## Open Questions

- 新同义改写基准是否足以证明需要模型/Embedding，由实现阶段的失败分析决定；本变更不预设答案。
- 外部可靠队列只有在 24 小时本地 outbox、Stop 延迟或多 worker 需求被真实数据触发后再选型。
