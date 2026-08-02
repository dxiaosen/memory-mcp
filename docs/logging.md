# Memory MCP 日志规范

## 1. 目标

日志用于定位服务启动、MCP 调用、捕获、审核、数据库迁移和健康检查问题。默认
模式只记录运行元数据；手工联调可显式开启内容模式，直接观察通过敏感检查后的
捕获、准入、持久化和召回内容。

实现位于：

```text
server/src/memory_mcp/logging.py               # Server/Core/DB
agent/src/memory_mcp_agent/logging.py           # Agent Hook
```

## 2. 输出与配置

本地默认输出到终端和 `.memory-mcp/logs/memory-mcp.log`。文件使用滚动日志，
默认单文件 10 MiB，保留 5 个历史文件。

MCP 服务使用：

```dotenv
MEMORY_MCP_LOG_LEVEL=INFO
MEMORY_MCP_LOG_CONTENT=false
MEMORY_MCP_LOG_FILE=.memory-mcp/logs/memory-mcp.log
MEMORY_MCP_LOG_MAX_BYTES=10485760
MEMORY_MCP_LOG_BACKUP_COUNT=5
```

`MEMORY_MCP_LOG_FILE` 可以设为空值对应的运行配置，以便只使用终端或 systemd
journal。ECS 示例写入 `/var/log/memory-mcp/memory-mcp.log`。

固定和真实候选抽取都在 MCP 服务进程中运行，统一使用上述
`MEMORY_MCP_LOG_*` 配置。独立 `memory-mcp-agent` 不导入 Server 日志模块，也不
支持内容日志。它固定把非内容阶段日志写到
stderr 和当前进程工作目录的 `.memory-mcp/logs/agent-hook.log`，不要求用户增加
日志配置。

需要手工查看核心流程时临时设置：

```dotenv
MEMORY_MCP_LOG_CONTENT=true
```

服务启动会写入 `logging.content.enabled` 警告，明确指出当前日志会持久化业务
内容。修改配置后必须重启进程。

## 3. 格式

每条事件是一行可检索的 `event + key=value`：

```text
2026-07-30T14:20:31+0800 INFO memory_mcp.tools:
event="memory.mcp.tool.completed" duration_ms=7.413 owner_ref="..."
request_id="..." result_count=1 status="completed" tool_name="list_memories"
```

实际文件不会换行。字段按名称排序。

常用字段：

| 字段 | 含义 |
| --- | --- |
| `event` | 稳定事件名 |
| `request_id` | MCP request id |
| `owner_ref` | owner 的稳定假名引用 |
| `client_ref` / `agent_ref` | 调用方稳定假名引用 |
| `capture_id` / `memory_id` / `revision_id` | 技术记录 ID |
| `status` / `error_code` | 稳定状态 |
| `relation_origin` / `relation_scope` | 关系来源和 item/revision 作用域 |
| `relation_count` / `stale_relation_count` | 新建关系和 replacement/到期失效关系数量 |
| `candidate_count` / `lexical_count` / `recent_count` | 混合召回候选计数 |
| `expired_memory_count` / `expired_review_count` | 单批维护状态转换计数 |
| `result_count` | 结果数量 |
| `duration_ms` | 操作耗时 |
| `error_type` | 异常类型名，不是异常消息 |

`stable_reference()` 使用截断 SHA-256 避免直接输出 identifier。它不是匿名化机制；
低熵 identifier 仍可能被枚举，因此日志访问权限仍需受控。

## 4. 两种日志模式

默认 `LOG_CONTENT=false` 时不得记录：

- user、assistant 或 tool 消息正文；
- candidate、pending、memory 或 Evidence 正文；
- `source_expression`；

`LOG_CONTENT=true` 时，专用内容事件可以记录：

- SensitiveGuard 脱敏后的本轮输入和 subject hint；
- 通过敏感检查的候选及其 source expression；
- 准入结果、持久化 memory/review/evidence 结构；
- 当前 owner 范围内的召回查询、排序记录和输出内容。

以下内容在任何模式、任何级别都不得记录：

- Bearer Token、数据库 DSN、密码、模型 API Key；
- backend 异常消息；
- 敏感规则命中的原文。

`log_event()` 会按字段名自动遮盖 `query`、`prompt`、`answer`、`content`、
`source_expression`、`api_key`、`password` 和 `secret`，以及常见 secret 后缀。
这只是最后防线；调用方不能把完整对象塞进 `payload`、`details` 等未受保护字段。

捕获流程处理未知 backend 异常时只记录 `type(exc).__name__`，不能调用会附带异常
正文的 `logger.exception()`。

内容模式由独立的 `log_content_event()` 输出，调用方只能传入已经通过敏感边界的
对象。它不会替代 SensitiveGuard，也不会解除上述 Secret 禁止项。

## 5. 当前事件

服务与 MCP：

```text
memory.mcp.server.starting
memory.mcp.tool.started
memory.mcp.tool.completed
memory.mcp.tool.failed
```

Core：

```text
memory.profile.registered
memory.create.started
memory.create.completed
memory.create.blocked
memory.get.completed
memory.get.unavailable
memory.list.completed
memory.revoke.completed
memory.relation.linked
memory.relation.revoked
memory.capture.started
memory.capture.relations_planned
memory.capture.completed
memory.capture.incomplete
memory.capture.processing_failed
memory.review.confirmed
memory.review.rejected
memory.recall.candidates
memory.maintenance.completed
memory.maintenance.failed
```

内容模式：

```text
logging.content.enabled
memory.capture.input
memory.capture.candidates
memory.capture.admission
memory.capture.relation_candidates
memory.capture.persisted
memory.create.input
memory.create.persisted
memory.read.get
memory.read.history
memory.read.list
memory.review.list
memory.review.get
memory.review.confirmed
memory.review.rejected
memory.recall.input
memory.recall.ranked
memory.recall.output
```

持久化与运维：

```text
memory.postgresql.profile_registered
memory.postgresql.record_committed
memory.postgresql.relation_linked
memory.postgresql.capture_committed
memory.postgresql.migration.started
memory.postgresql.migration.applied
memory.postgresql.health_check.completed
```

Agent Host：

```text
agent_hook.started
agent_hook.recall.completed
agent_hook.capture.completed
agent_hook.capture.skipped
agent_hook.failed
```

Agent Hook 事件只记录 host、事件名、稳定 run reference、数量、状态、尝试次数和
错误码，不记录 prompt、最终回复、Token 或本地状态内容。

`memory.capture.relations_planned` 只记录 capture ID、模型/prompt/schema 版本以及
endpoint/proposal/accepted/skipped 数量，不记录端点 subject/content、关系原文或
owner。`memory.capture.relation_candidates` 只在操作者显式启用内容日志时输出已经
通过脱敏边界的建议和计划关系；关闭内容日志时不会生成该正文事件。
`memory.postgresql.capture_committed` 的 `stale_relation_count` 只表示本次 replacement
物化失效的边数；`memory.relation.linked/revoked` 可以记录 origin/scope/status，但
任何 operational event 都不得记录 provenance 的 conversation、turn 或 source expression。

`memory.recall.candidates` 只记录 Profile、候选硬上限和 lexical/recent 数量；不记录
query 或候选正文。`memory.maintenance.completed/failed` 只记录耗时、状态转换计数、
`has_more` 或异常类型名，不记录 owner、review、memory 或 relation 标识。

已删除的 Knowledge、Agent、旧 CLI 和 bootstrap 事件不再属于本项目。

## 6. 新增日志

```python
import logging
from time import perf_counter

from memory_mcp.logging import log_event

_LOGGER = logging.getLogger(__name__)


def execute() -> None:
    started_at = perf_counter()
    try:
        # 业务逻辑
        pass
    except Exception as exc:
        log_event(
            _LOGGER,
            logging.ERROR,
            "component.execute.failed",
            error_type=type(exc).__name__,
        )
        raise
    log_event(
        _LOGGER,
        logging.INFO,
        "component.execute.completed",
        duration_ms=round((perf_counter() - started_at) * 1000, 3),
    )
```

事件名使用 `领域.对象.动作`，已经发布的事件名和字段名保持稳定。

新增业务内容日志时必须使用 `log_content_event()`，并确保数据已经通过身份限定和
敏感检查：

```python
from memory_mcp.logging import log_content_event

log_content_event(
    "memory.example.content",
    memory={"memory_id": memory.memory_id, "content": memory.content},
)
```

不要把 settings、HTTP headers、异常对象、数据库连接参数或未经 SensitiveGuard
检查的原始 payload 传给该函数。

Agent 包新增运行事件时从 `memory_mcp_agent.logging` 导入 `log_event`。Agent
日志永远不接受 `content=True`，也不得为复用 Server formatter 而反向依赖
`memory_mcp.logging`。

## 7. 查看日志

Linux：

```bash
tail -n 50 .memory-mcp/logs/memory-mcp.log
tail -f .memory-mcp/logs/memory-mcp.log
tail -f .memory-mcp/logs/agent-hook.log
journalctl -u memory-mcp.service -f
```

PowerShell：

```powershell
Get-Content .memory-mcp/logs/memory-mcp.log -Tail 50
Get-Content .memory-mcp/logs/memory-mcp.log -Wait
```

当前实现是单进程文本日志，不包含集中式日志平台、trace/span、metrics、远程上传
或自动告警。未来增加这些能力时，内容模式也必须保持显式、独立和默认关闭。

## 8. 手工联调收尾

联调结束后：

1. 将 `MEMORY_MCP_LOG_CONTENT` 恢复为 `false`；
2. 重启服务并确认 `logging.configured` 的 `content_logging=false`；
3. 按所在环境的数据管理要求删除或归档内容日志；
4. 若日志意外包含 Secret，停止服务并轮换对应凭据，不能只删除文件。

内容日志是诊断能力，不是记忆存储、审计账本或长期业务数据出口。
