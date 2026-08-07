# Memory MCP 日志规范

日志用于定位服务启动、MCP 调用、捕获、召回、审核、数据库迁移和健康检查问题。实现位于
`server/src/memory_mcp/core/support/logging.py`（Server/Core/DB 的权威实现，根包
`memory_mcp.logging` 是别名）和 `agent/src/memory_mcp_agent/logging.py`（Agent Hook）。

| 模式 | 说明 |
| --- | --- |
| 默认模式 | 只记录运行元数据（阶段、引用、数量、状态、错误码、耗时） |
| 内容模式 | `MEMORY_MCP_LOG_CONTENT=true` 开启；观察通过敏感检查后的捕获、准入、持久化和召回内容 |

## 1. 输出与配置

本地默认输出到终端和 `.memory-mcp/logs/memory-mcp.log`，滚动日志单文件 10 MiB、
保留 5 个历史文件。

```dotenv
MEMORY_MCP_LOG_LEVEL=INFO
MEMORY_MCP_LOG_CONTENT=false
MEMORY_MCP_LOG_FILE=.memory-mcp/logs/memory-mcp.log
MEMORY_MCP_LOG_MAX_BYTES=10485760
MEMORY_MCP_LOG_BACKUP_COUNT=5
```

| 配置说明 | 行为 |
| --- | --- |
| `MEMORY_MCP_LOG_FILE` 设为空值 | 只使用终端或 systemd journal |
| ECS 示例 | 写入 `/var/log/memory-mcp/memory-mcp.log` |
| 修改配置后 | 必须重启进程 |
| 开启内容模式 | 服务启动写入 `logging.content.enabled` 警告 |

`memory-mcp-agent` 不导入 Server 日志模块，不支持内容日志，固定写到 stderr 和
`<cwd>/.memory-mcp/logs/agent-hook.log`。

## 2. 格式与字段

每条事件是一行可检索的 `event + key=value`，字段按名称排序。示例：
`event="memory.capture.completed" auto_saved_count=1 capture_id="..." duration_ms=3.21 owner_ref="..." replayed=false`

| 字段 | 含义 | 字段 | 含义 |
| --- | --- | --- | --- |
| `event` | 稳定事件名 | `relation_origin` / `relation_scope` | 关系来源和 item/revision 作用域 |
| `request_id` | MCP request id | `relation_count` / `stale_relation_count` | 新建关系和失效关系数量 |
| `owner_ref` | owner 的稳定假名引用 | `candidate_count` / `lexical_count` / `vector_count` / `recent_count` | 召回候选计数 |
| `client_ref` / `agent_ref` | 调用方稳定假名引用 | `expired_memory_count` / `expired_review_count` | 维护状态转换计数 |
| `capture_id` / `memory_id` / `revision_id` | 技术记录 ID | `result_count` / `duration_ms` | 结果数量 / 操作耗时 |
| `recall_ref` | 召回稳定关联标识（仅日志，不改 MCP 返回契约） | `auto_saved_count` / `pending_count` / `discarded_count` / `blocked_count` | 准入四类计数 |
| `status` / `error_code` | 稳定状态 | `error_type` | 异常类型名，不是异常消息 |

`stable_reference()` 使用截断 SHA-256 避免直接输出 identifier，但不是匿名化机制；
低熵 identifier 仍可能被枚举，日志访问权限仍需受控。

## 3. 两种日志模式

| | 默认 `false` | 内容 `true` |
| --- | --- | --- |
| 运行元数据（阶段、引用、数量、状态、错误码、耗时） | ✅ | ✅ |
| SensitiveGuard 脱敏后的本轮输入和 subject hint | ❌ | ✅ |
| 通过敏感检查的候选及 source expression | ❌ | ✅ |
| 准入结果、持久化 memory/review/evidence 结构 | ❌ | ✅ |
| 当前 owner 范围内的召回查询、排序记录和输出内容 | ❌ | ✅ |
| Bearer Token / DSN / 密码 / API Key / backend 异常消息 / 敏感规则命中的原文 | ❌ | ❌ |

自动遮盖与边界约束：

| 机制 | 说明 |
| --- | --- |
| `log_event()` 自动遮盖 | `query`/`prompt`/`answer`/`content`/`source_expression`/`api_key`/`password`/`secret` 及常见 secret 后缀（最后防线） |
| 调用方约束 | 不能把完整对象塞进 `payload`/`details` 等未受保护字段 |
| backend 异常 | 只记录 `type(exc).__name__`，不调用附带异常正文的 `logger.exception()` |
| `log_content_event()` | 只接受已通过敏感边界的对象，不替代 SensitiveGuard |

## 4. 链路关联

通过以下字段还原一次完整流程：

```
Agent Hook (run_ref)
  → MCP Tool (request_id)
    → Core Capture (capture_id) 或 Recall (recall_ref)
      → PostgreSQL commit (capture_id)
```

- `run_ref`：Agent Hook 的顶层轮次标识；
- `request_id`：MCP 工具调用标识；
- `capture_id`：捕获事务标识，贯穿 Capture started→completed→PostgreSQL commit；
- `recall_ref`：召回的稳定关联标识（仅日志，不改 MCP 返回契约），贯穿 recall started→completed；
- `owner_ref`：owner 稳定假名，跨所有层关联。

## 5. 事件表

### 服务与 MCP

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.mcp.server.starting` | INFO | `host`, `port`, `mcp_path` |
| `memory.mcp.server.stopped` | INFO | `reason` |
| `memory.mcp.tool.started` | INFO | `request_id`, `client_ref`, `owner_ref`, `tool_name` |
| `memory.mcp.tool.completed` | INFO | `request_id`, `duration_ms`, `status`, `result_count`, `tool_name` |
| `memory.mcp.tool.failed` | ERROR/WARNING | `request_id`, `error_code`, `error_type`, `tool_name` |

### Capture 阶段

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.capture.started` | INFO | `capture_id`, `owner_ref`, `profile_id`, `profile_version`, `was_reprocessed`, `event_id`, `message_count`, `input_character_count` |
| `memory.capture.replay` | INFO | `capture_id`, `owner_ref`, `status`, `replayed` |
| `memory.capture.idempotency_conflict` | WARNING | `capture_id`, `owner_ref`, `event_id` |
| `memory.capture.completed` | INFO | `capture_id`, `owner_ref`, `profile_id`, `replayed`, `was_reprocessed`, `duration_ms`, `candidate_count`, `auto_saved_count`, `pending_count`, `discarded_count`, `blocked_count`, `reason_counts`, `duplicate_count`, `replacement_count`, `review_count`, `relation_proposal_count`, `relation_accepted_count`, `relation_skipped_count`, `failure_code` |
| `memory.capture.incomplete` | WARNING | `capture_id`, `owner_ref`, `profile_id`, `status`, `failure_code`, `was_reprocessed`, `duration_ms` |
| `memory.capture.processing_failed` | ERROR | `capture_id`, `error_type`, `owner_ref` |
| `memory.capture.relations_planned` | INFO | `capture_id`, 模型/prompt/schema 版本, endpoint/proposal/accepted/skipped 数量 |

Capture 内容模式事件（仅 `LOG_CONTENT=true`）：

| 事件名 | 记录内容 |
| --- | --- |
| `memory.capture.input` | 脱敏输入、messages、subject_hint |
| `memory.capture.candidates` | 候选及 source expression |
| `memory.capture.admission` | 准入结果 |
| `memory.capture.relation_candidates` | 脱敏建议和计划关系 |
| `memory.capture.persisted` | 持久化结构（memory/review/duplicate/replacement/relation） |

### Recall 阶段

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.recall.started` | INFO | `recall_ref`, `owner_ref`, `profile_id`, `embedding_enabled`, `max_items`, `token_budget` |
| `memory.recall.candidates` | DEBUG | `recall_ref`, `candidate_count`, `candidate_limit`, `lexical_count`, `vector_count`, `recent_count`, `profile_id`, `embedding_degraded` |
| `memory.recall.embedding_failed` | WARNING | `error_type` |
| `memory.recall.completed` | INFO | `recall_ref`, `owner_ref`, `profile_id`, `duration_ms`, `result_count`, `estimated_tokens`, `token_budget`, `truncated`, `zero_result`, `candidate_count`, `lexical_count`, `vector_count`, `recent_count`, `threshold_passed_count`, `relation_boosted_count`, `embedding_enabled`, `embedding_degraded` |
| `memory.recall.timeline.started` | INFO | `recall_ref`, `owner_ref`, `profile_id`, `focus_memory_id`, `max_hops`, `token_budget` |
| `memory.recall.timeline.completed` | INFO | `recall_ref`, `owner_ref`, `profile_id`, `hop_count`, `estimated_tokens`, `token_budget`, `truncated` |

Recall 内容模式事件（仅 `LOG_CONTENT=true`）：

| 事件名 | 记录内容 |
| --- | --- |
| `memory.recall.input` | 脱敏查询、subject、task_intent |
| `memory.recall.ranked` | 排序记录 |
| `memory.recall.output` | 召回输出、rendered_context |
| `memory.recall.timeline.output` | 时间线焦点记忆、hops、rendered_context |

### Core 操作与状态变化

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.profile.registered` | INFO | `profile_id`, `memory_type_count` |
| `memory.create.started` | DEBUG | `memory_type`, `owner_ref`, `profile_id` |
| `memory.create.completed` | INFO | `duration_ms`, `evidence_count`, `lifecycle_status`, `memory_id`, `owner_ref`, `revision_id`, `profile_id` |
| `memory.create.blocked` | WARNING | `blocked_categories`, `owner_ref`, `profile_id` |
| `memory.get.completed` / `.unavailable` | INFO | `owner_ref`, `memory_id`/`error_code` |
| `memory.list.completed` | INFO | `include_inactive`, `owner_ref`, `result_count` |
| `memory.search.started` | DEBUG | `owner_ref`, `profile_id`, `memory_type`, `limit` |
| `memory.search.completed` | INFO | `owner_ref`, `result_count` |
| `memory.stats.completed` | INFO | `owner_ref`, `total_memories`, `pending_count` |
| `memory.revoke.completed` | INFO | `lifecycle_status`, `memory_id`, `owner_ref` |
| `memory.relation.linked` | INFO | `relation_id`, `relation_origin`, `relation_scope`, `relation_type`, `source_memory_id`, `target_memory_id` |
| `memory.relation.revoked` | INFO | `relation_id`, `relation_origin`, `relation_scope`, `relation_type` |
| `memory.review.confirmed` / `.rejected` | INFO | `review_id`, `owner_ref`, `promoted_to_team` |
| `memory.review.batch_confirmed` | INFO | `owner_ref`, `confirmed_count`, `failed_count` |
| `memory.maintenance.completed` / `.failed` | INFO/ERROR | `duration_ms`, 状态转换计数, `expired_relation_context_count`, `reminder_count`, `has_more` / `error_type` |
| `memory.maintenance.reminder_written` | INFO | `owner_ref`, `profile_id`, `relation_type`, `focus_memory_id`, `reminder_memory_type` |
| `memory.team_extraction.completed` / `.batch_completed` / `.failed` | INFO/ERROR | `team_owner_ref`, `member/memory/cluster/candidate_count`, `duration_ms` / `error_type` |
| `memory.embedding.completed` | DEBUG | — |

Core 读取内容模式事件：

| 事件名 | 记录内容 |
| --- | --- |
| `memory.create.input` / `.persisted` | 创建输入与持久化结构 |
| `memory.read.get` / `.history` / `.list` / `.search` | 读取记录（get/history/list/关键词检索） |
| `memory.review.list` / `.get` / `.confirmed` / `.rejected` | 评审记录 |

### 持久化与运维

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.postgresql.profile_registered` | INFO | — |
| `memory.postgresql.record_committed` | INFO | `memory_id` |
| `memory.postgresql.relation_linked` | INFO | `relation_count` |
| `memory.postgresql.capture_committed` | INFO | `capture_id`, `stale_relation_count` |
| `memory.postgresql.capture_replayed` | INFO | `capture_id` |
| `memory.postgresql.migration.started` / `.applied` / `.rebuild` | INFO | — |
| `memory.postgresql.health_check.completed` | INFO | `status` |

### Agent Host

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `agent_hook.started` | INFO | `run_ref` |
| `agent_hook.recall.completed` | INFO | `run_ref`, `recalled_count`, `status` |
| `agent_hook.capture.completed` / `.skipped` | INFO | `run_ref`, `status` / `reason` |
| `agent_hook.pending_retry.completed` | INFO | `run_ref`, `attempts` |
| `agent_hook.pending_retry.failed` / `agent_hook.failed` | ERROR | `run_ref`, `attempts`, `warning_code` / `error_type` |

Agent Hook 事件不记录 prompt、最终回复、Token 或本地状态内容。

## 6. 日志级别

| 级别 | 使用场景 |
| --- | --- |
| INFO | 正常开始、完成、状态变化和聚合结果 |
| WARNING | 可恢复降级、重试、fail-open、embedding 不可用、幂等冲突 |
| ERROR | 请求失败、事务失败、不可恢复错误 |
| DEBUG | 不含敏感正文的细粒度对象状态（候选计数、召回候选分布） |

pending、discard、blocked 和 zero-result 本身不属于异常，不记 WARNING。

## 7. 新增日志

```python
from time import perf_counter
from memory_mcp.core.support import log_event
# started → try 业务逻辑 → except: log_event(..., error_type=type(exc).__name__) raise
# → log_event(..., duration_ms=round((perf_counter()-started_at)*1000, 3))
```

| 规则 | 说明 |
| --- | --- |
| 事件名 | 使用 `领域.对象.动作`；已发布事件名和字段名保持稳定 |
| 内容日志 | 必须用 `log_content_event()`，确保数据已通过身份限定和敏感检查 |
| 禁止传入 | settings、HTTP headers、异常对象、数据库连接参数、未经 SensitiveGuard 检查的原始 payload |
| Agent 包 | 从 `memory_mcp_agent.logging` 导入 `log_event`，永远不接受 `content=True`，不得反向依赖 `memory_mcp.logging` |
| 同一错误 | 只在最有业务上下文的一层记录一次 |

## 8. 常用 grep 示例

```bash
# 还原一次完整 Capture 流程
grep "capture_id=<id>" .memory-mcp/logs/memory-mcp.log

# 还原一次完整 Recall 流程
grep "recall_ref=<ref>" .memory-mcp/logs/memory-mcp.log

# 查所有失败
grep "event=\"memory.*failed\"\|event=\"memory.*incomplete\"" .memory-mcp/logs/memory-mcp.log

# 查 embedding 降级
grep "embedding_degraded=true" .memory-mcp/logs/memory-mcp.log

# 查 Agent Hook 轮次
grep "run_ref=<ref>" .memory-mcp/logs/agent-hook.log
```

## 9. 查看日志、限制与联调收尾

```bash
tail -n 50 .memory-mcp/logs/memory-mcp.log      # 或 tail -f 持续跟踪
tail -f .memory-mcp/logs/agent-hook.log
journalctl -u memory-mcp.service -f
```

当前实现是单进程文本日志，不含集中式日志平台、trace/span、metrics、远程上传或
自动告警。未来增加这些能力时，内容模式也必须保持显式、独立和默认关闭。

| 联调收尾步骤 | 说明 |
| --- | --- |
| 1. 关闭内容模式 | 将 `MEMORY_MCP_LOG_CONTENT` 恢复为 `false` |
| 2. 重启并确认 | 重启服务并确认 `logging.configured` 的 `content_logging=false` |
| 3. 处理内容日志 | 按数据管理要求删除或归档内容日志 |
| 4. 处理 Secret 泄漏 | 若日志意外包含 Secret，停止服务并轮换对应凭据，不能只删除文件 |

内容日志是诊断能力，不是记忆存储、审计账本或长期业务数据出口。
