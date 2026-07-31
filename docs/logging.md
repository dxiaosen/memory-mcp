# Memory MCP 日志规范

## 1. 目标

日志用于定位服务启动、MCP 调用、捕获、审核、数据库迁移和健康检查问题。默认
模式只记录运行元数据；手工联调可显式开启内容模式，直接观察通过敏感检查后的
捕获、准入、持久化和召回内容。

实现位于：

```text
src/memory_mcp/logging.py
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
`MEMORY_MCP_LOG_*` 配置。Hook Client 不记录业务正文或 Secret。

需要手工查看核心流程时临时设置：

```dotenv
MEMORY_MCP_LOG_CONTENT=true
```

服务启动会写入 `logging.content.enabled` 警告，明确指出当前日志会持久化业务
内容。修改配置后必须重启进程。

## 3. 格式

每条事件是一行可检索的 `event + key=value`：

```text
2026-07-30T14:20:31+0800 INFO memory_mcp.server.tools:
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
memory.scenario.registered
memory.create.started
memory.create.completed
memory.create.blocked
memory.get.completed
memory.get.unavailable
memory.list.completed
memory.capture.started
memory.capture.completed
memory.capture.incomplete
memory.capture.processing_failed
memory.review.confirmed
memory.review.rejected
```

内容模式：

```text
logging.content.enabled
memory.capture.input
memory.capture.candidates
memory.capture.admission
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
memory.postgresql.scenario_registered
memory.postgresql.record_committed
memory.postgresql.capture_committed
memory.postgresql.migration.started
memory.postgresql.migration.applied
memory.postgresql.health_check.completed
```

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

## 7. 查看日志

Linux：

```bash
tail -n 50 .memory-mcp/logs/memory-mcp.log
tail -f .memory-mcp/logs/memory-mcp.log
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
