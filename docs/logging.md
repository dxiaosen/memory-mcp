# Memory MCP 日志规范

日志用于定位服务启动、MCP 调用、捕获、审核、数据库迁移和健康检查问题。实现位于
`server/src/memory_mcp/logging.py`（Server/Core/DB）和
`agent/src/memory_mcp_agent/logging.py`（Agent Hook）。

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
`event="memory.mcp.tool.completed" duration_ms=7.413 owner_ref="..." request_id="..." result_count=1 status="completed" tool_name="list_memories"`

| 字段 | 含义 | 字段 | 含义 |
| --- | --- | --- | --- |
| `event` | 稳定事件名 | `relation_origin` / `relation_scope` | 关系来源和 item/revision 作用域 |
| `request_id` | MCP request id | `relation_count` / `stale_relation_count` | 新建关系和失效关系数量 |
| `owner_ref` | owner 的稳定假名引用 | `candidate_count` / `lexical_count` / `recent_count` | 混合召回候选计数 |
| `client_ref` / `agent_ref` | 调用方稳定假名引用 | `expired_memory_count` / `expired_review_count` | 维护状态转换计数 |
| `capture_id` / `memory_id` / `revision_id` | 技术记录 ID | `result_count` / `duration_ms` | 结果数量 / 操作耗时 |
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

## 4. 事件表

### 服务与 MCP

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.mcp.server.starting` | INFO | — |
| `memory.mcp.tool.started` / `.completed` | INFO | `request_id`[, `duration_ms`,`status`,`result_count`,`tool_name`] |
| `memory.mcp.tool.failed` | ERROR | `request_id`, `error_code`, `error_type` |

### Core

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.profile.registered` | INFO | `profile_id` |
| `memory.create.started` / `.completed` / `.blocked` | INFO | `owner_ref`, [`memory_id`,`revision_id`]/[`error_code`] |
| `memory.get.completed` / `.unavailable` | INFO | `owner_ref`, `memory_id`/`error_code` |
| `memory.list.completed` | INFO | `owner_ref`, `result_count` |
| `memory.revoke.completed` | INFO | `owner_ref`, `memory_id` |
| `memory.relation.linked` / `.revoked` | INFO | `relation_origin`, `relation_scope`[, `relation_count`] |
| `memory.capture.started` | INFO | `capture_id` |
| `memory.capture.relations_planned` | INFO | `capture_id`, 模型/prompt/schema 版本, endpoint/proposal/accepted/skipped 数量 |
| `memory.capture.completed` / `.incomplete` / `.processing_failed` | INFO/ERROR | `capture_id`, [`memory_id`]/[`error_code`]/[`error_type`] |
| `memory.review.confirmed` / `.rejected` | INFO | `review_id` |
| `memory.recall.candidates` | INFO | Profile, 候选硬上限, `lexical_count`, `recent_count` |
| `memory.recall.embedding_failed` | WARNING | `error_type`（查询向量计算失败，降级为两路） |
| `memory.maintenance.completed` / `.failed` | INFO/ERROR | `duration_ms`, 状态转换计数, `has_more` / `error_type` |
| `memory.team_extraction.completed` / `.batch_completed` / `.failed` | INFO/ERROR | `team_owner_ref`, `member/memory/cluster/candidate_count`, `duration_ms` / `error_type` |

Core 字段约束：

| 事件 | 约束 |
| --- | --- |
| `relations_planned` | 不记录端点 subject/content、关系原文或 owner |
| `relation.linked/revoked` | 可记录 origin/scope/status，不得记录 provenance 的 conversation/turn/source expression |
| `recall.candidates` | 不记录 query 或候选正文 |
| `maintenance.*` | 不记录 owner/review/memory/relation 标识 |

### 内容模式（仅 `LOG_CONTENT=true`）

| 事件名 | 记录内容 |
| --- | --- |
| `logging.content.enabled` | 启用警告 |
| `memory.capture.input` / `.candidates` / `.admission` / `.relation_candidates` / `.persisted` | 脱敏输入 / 候选及 source expression / 准入结果 / 脱敏建议和计划关系 / 持久化结构 |
| `memory.create.input` / `.persisted` | 创建输入与持久化结构 |
| `memory.read.get` / `.history` / `.list` | 读取记录 |
| `memory.review.list` / `.get` / `.confirmed` / `.rejected` | 评审记录 |
| `memory.recall.input` / `.ranked` / `.output` | 召回查询、排序和输出 |

`log_content_event()` 只接受已通过敏感边界的对象，不替代 SensitiveGuard。

### 持久化与运维

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.postgresql.profile_registered` | INFO | — |
| `memory.postgresql.record_committed` | INFO | `memory_id` |
| `memory.postgresql.relation_linked` | INFO | `relation_count` |
| `memory.postgresql.capture_committed` | INFO | `capture_id`, `stale_relation_count` |
| `memory.postgresql.migration.started` / `.applied` | INFO | — |
| `memory.postgresql.health_check.completed` | INFO | `status` |

`stale_relation_count` 表示本次 replacement 物化失效的边数。`/health` 的 maintenance
快照只包含状态、连续失败次数、时间和异常类型，不包含异常消息。

### Agent Host

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `agent_hook.started` | INFO | `run_ref` |
| `recall.completed` | INFO | `run_ref`, `recalled_count`, `status` |
| `capture.completed` / `.skipped` | INFO | `run_ref`, `status` / `reason` |
| `pending_retry.completed` | INFO | `run_ref`, `attempts` |
| `pending_retry.failed` / `agent_hook.failed` | ERROR | `run_ref`, `attempts`, `warning_code` / `error_type` |

Agent Hook 事件不记录 prompt、最终回复、Token 或本地状态内容。已删除的 Knowledge、
Agent、旧 CLI 和 bootstrap 事件不再属于本项目。

## 5. 新增日志

```python
from time import perf_counter
from memory_mcp.logging import log_event
# started → try 业务逻辑 → except: log_event(..., error_type=type(exc).__name__) raise
# → log_event(..., duration_ms=round((perf_counter()-started_at)*1000, 3))
```

| 规则 | 说明 |
| --- | --- |
| 事件名 | 使用 `领域.对象.动作`；已发布事件名和字段名保持稳定 |
| 内容日志 | 必须用 `log_content_event()`，确保数据已通过身份限定和敏感检查 |
| 禁止传入 | settings、HTTP headers、异常对象、数据库连接参数、未经 SensitiveGuard 检查的原始 payload |
| Agent 包 | 从 `memory_mcp_agent.logging` 导入 `log_event`，永远不接受 `content=True`，不得反向依赖 `memory_mcp.logging` |

## 6. 查看日志、限制与联调收尾

```bash
tail -n 50 .memory-mcp/logs/memory-mcp.log      # 或 tail -f 持续跟踪
tail -f .memory-mcp/logs/agent-hook.log
journalctl -u memory-mcp.service -f
# PowerShell: Get-Content .memory-mcp/logs/memory-mcp.log -Tail 50 / -Wait
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
