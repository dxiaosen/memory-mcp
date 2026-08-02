## Why

当前召回会为每个候选单独加载 Evidence，默认配置下可能把一次 BeforeRun 放大为数百次 SQL；同时 Agent 会把服务端 `reprocess_required` 当作成功并删除唯一的重试 payload，跨实例重叠捕获也可能把同一 payload 的后提交者错误映射为无效请求。这些问题在小规模单进程测试中不可见，但会直接影响真实投研使用中的延迟、记忆完整性和幂等承诺，因此应在继续扩展业务能力前收口。

## What Changes

- 将召回改为两阶段读取：Repository 只返回无 Evidence 的有界候选，应用层完成确定性排序后再一次性批量加载最终命中的最多三条来源，消除候选数量相关的 N+1。
- 让捕获提交返回数据库中的权威结果；两个进程重叠处理同一 payload 时最多提交一次，后提交者得到 `replayed=true`，不同 payload 仍稳定冲突。
- Agent 在首次 Stop 时原子保存完整捕获 payload 和固定 `observed_at`；仅在终态成功/永久失败后删除。网络失败或 `reprocess_required` 保留本地状态，并在同项目后续 Hook 中有界重投，不引入外部队列或常驻 worker。
- 对永久 `failed` 和可重处理结果输出稳定 warning，并记录不含正文的投递状态日志。
- 维护 runner 暴露无正文的最近成功时间、连续失败次数和降级状态，使 `/health` 能区分数据库健康与维护滞后，同时保持到期读取过滤不依赖 runner 成功。
- 扩充投研召回评测：增加空召回、同义改写、困难实体/报告期干扰和大候选集；增加 PostgreSQL 实际执行的维护/混合召回合同，并记录查询次数与延迟基线。
- 调和设计文档中的投递、跨进程幂等和生命周期边界；清理已失效的 OpenSpec 延期口径，并为后续正式发布准备一致版本记录。

## Capabilities

### New Capabilities

- `efficient-memory-recall`: 规定有界候选、延迟 Evidence 水合、严格 owner/Profile 隔离、查询数量上限和真实 PostgreSQL 验证。
- `reliable-agent-delivery`: 规定捕获终态、可重处理状态、本地持久重投、固定 payload 身份以及跨进程最多一次提交。
- `maintenance-operational-health`: 规定维护 runner 的成功/失败状态、健康响应和无正文可观测性。

### Modified Capabilities

无。当前仓库尚未归档生成主规范；本变更以独立能力记录增量合同，归档时再与既有变更统一同步。

## Impact

- Server Core：Recall candidate 合同、RecallService 最终水合、capture commit 返回值和错误语义。
- PostgreSQL：召回批量 Evidence 查询；不新增业务正文表。若本地重投不需要 Server schema，则不新增 migration。
- Agent：短期状态 schema 向后兼容增加 final output、固定 observed time 和 pending delivery 状态；Hook 配置仍只要求 URL 和 Token。
- HTTP：现有 MCP 工具名称和输入 schema 不变；`/health` 增加 maintenance 子状态字段。
- 测试与评测：复用现有测试文件，新增跨 Service 并发、重投和 PostgreSQL 查询合同，不创建重复测试目录。
- 非目标：外部消息队列、常驻 Agent daemon、Embedding/向量库、LLM rerank、自动事实核验、物理删除和生产 OAuth/OIDC。
