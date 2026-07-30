# Memory MCP 日志规范

## 1. 目标

日志用于定位服务启动、MCP 调用、捕获、审核、数据库迁移和健康检查问题，不用于
保存业务正文或展示正常工具结果。

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
MEMORY_MCP_LOG_FILE=.memory-mcp/logs/memory-mcp.log
MEMORY_MCP_LOG_MAX_BYTES=10485760
MEMORY_MCP_LOG_BACKUP_COUNT=5
```

`MEMORY_MCP_LOG_FILE` 可以设为空值对应的运行配置，以便只使用终端或 systemd
journal。ECS 示例写入 `/var/log/memory-mcp/memory-mcp.log`。

离线模型和过渡 SQLite 工具使用同结构的 `LOG_*` 配置，因为它们不经过
`MemoryServerSettings`。

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

## 4. 禁止内容

任何级别都不得记录：

- Bearer Token、数据库 DSN、密码、模型 API Key；
- user、assistant 或 tool 消息正文；
- candidate、pending、memory 或 Evidence 正文；
- `source_expression`；
- backend 异常消息；
- 敏感规则命中的原文。

`log_event()` 会按字段名自动遮盖 `query`、`prompt`、`answer`、`content`、
`source_expression`、`api_key`、`password` 和 `secret`，以及常见 secret 后缀。
这只是最后防线；调用方不能把完整对象塞进 `payload`、`details` 等未受保护字段。

捕获流程处理未知 backend 异常时只记录 `type(exc).__name__`，不能调用会附带异常
正文的 `logger.exception()`。

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

持久化与运维：

```text
memory.sqlite.scenario_registered
memory.sqlite.record_committed
memory.sqlite.capture_committed
memory.sqlite.migration.started
memory.sqlite.migration.applied
memory.sqlite.migration.skipped
memory.sqlite.migration.completed
memory.sqlite.health_check.completed
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
或自动告警。未来增加这些能力时仍不得把正文作为追踪字段。
