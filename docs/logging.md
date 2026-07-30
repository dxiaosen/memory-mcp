# 项目执行日志设计与使用说明

## 1. 目标

项目使用统一日志模块记录关键执行阶段、耗时、数量、状态和错误类型，帮助开发者
定位“执行到了哪里、在哪一步失败、耗时多少”。

日志模块不负责展示正常业务结果。CLI 的回答、来源和用户提示仍然使用 `print`；
日志负责开发诊断和运行追踪。

实现位置：

```text
src/agent_lab/observability/logging.py
```

## 2. 输出位置

默认同时输出到：

1. 当前终端；
2. `.agent-lab/logs/agent-lab.log`。

文件使用滚动日志，默认单个文件最大 10 MiB，保留 5 个历史文件：

```text
agent-lab.log
agent-lab.log.1
agent-lab.log.2
...
```

`.agent-lab` 已被 Git 忽略，日志不会提交到仓库。

## 3. 配置

```dotenv
LOG_LEVEL=INFO
LOG_FILE=.agent-lab/logs/agent-lab.log
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
```

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING` 或 `ERROR` |
| `LOG_FILE` | `.agent-lab/logs/agent-lab.log` | 滚动日志文件 |
| `LOG_MAX_BYTES` | `10485760` | 单个日志文件最大字节数 |
| `LOG_BACKUP_COUNT` | `5` | 保留的历史文件数量 |

日常运行建议使用 `INFO`。排查流程细节时临时设置：

```powershell
$env:LOG_LEVEL = "DEBUG"
```

## 4. 日志格式

日志采用单行、可检索的 `event + key=value` 格式：

```text
2026-07-29T14:20:31+0800 INFO agent_lab.memory.application.service:
event="memory.create.completed" duration_ms=7.413 evidence_count=1
lifecycle_status="active" memory_id="..." owner_ref="..."
```

实际文件中的一条事件不会换行。字段按名称排序，方便测试和日志检索。

常用字段：

| 字段 | 含义 |
| --- | --- |
| `event` | 稳定事件名称 |
| `duration_ms` | 本次操作耗时 |
| `owner_ref` | owner 的不可逆短哈希，不是原始用户标识 |
| `thread_ref` | thread id 的不可逆短哈希 |
| `memory_id`、`revision_id` | 诊断数据库记录使用的技术 ID |
| `result_count`、`source_count` | 返回数量 |
| `input_tokens`、`output_tokens` | 模型 Token 使用量 |
| `error_type` | 异常类型，不直接记录敏感正文 |

## 5. 日志级别

| 级别 | 使用方式 |
| --- | --- |
| `DEBUG` | 操作开始、依赖装配、迁移跳过、SQLite 提交等细节 |
| `INFO` | 索引完成、Agent 单轮完成、记忆创建/查询、迁移和健康检查 |
| `WARNING` | 可恢复但需要关注的异常状态，后续按具体流程增加 |
| `ERROR` | 命令失败和未预期异常 |

`INFO` 不会输出每个内部步骤；需要完整执行链时使用 `DEBUG`。

## 6. 敏感信息保护

日志不得记录：

- API Key、密码和 secret；
- 用户问题原文和模型回答原文；
- 记忆正文；
- Evidence 的 `source_expression`；
- 文档正文；
- 完整 owner 和 thread id。

`log_event()` 会自动对以下字段名脱敏：

```text
query
prompt
answer
content
source_expression
api_key
password
secret
```

例如：

```python
log_event(
    logger,
    logging.INFO,
    "example",
    query="内部问题",
)
```

最终输出：

```text
event="example" query="[REDACTED]"
```

owner 和 thread id 应先调用：

```python
stable_reference(identifier)
```

转换为稳定的 12 位哈希引用。它适合关联同一用户的多条执行日志，但不能恢复原始
identifier。

脱敏函数只是最后一道防线。新增日志时仍应遵循数据最小化原则，不要先把完整业务
对象、模型请求或环境配置传给日志函数。

## 7. 已接入的执行流程

### 7.1 CLI

```text
cli.command.started
cli.command.completed
cli.command.failed
cli.command.unexpected_failure
cli.agent_turn.failed
```

### 7.2 依赖装配

```text
bootstrap.knowledge_indexer.started
bootstrap.knowledge_indexer.completed
bootstrap.agent_service.started
bootstrap.agent_service.completed
```

只记录 provider、分块参数、Top-K 和递归限制，不记录凭证。

### 7.3 知识库索引

```text
knowledge.index.started
knowledge.index.completed
```

记录输入路径数量、是否重建、文档数、分块数、存储总数和耗时，不记录文件正文。

### 7.4 Agent

```text
agent.run.started
agent.run.completed
agent.thread.cleared
```

记录 thread 哈希、问题长度、来源数量、Token 数和耗时，不记录问题与回答正文。

### 7.5 Memory Core

```text
memory.scenario.registered
memory.create.started
memory.create.completed
memory.create.blocked
memory.get.completed
memory.get.unavailable
memory.list.completed
memory.sqlite.scenario_registered
memory.sqlite.record_committed
memory.postgresql.scenario_registered
memory.postgresql.record_committed
memory.capture.started
memory.capture.completed
memory.capture.incomplete
memory.capture.processing_failed
memory.review.confirmed
memory.review.rejected
memory.sqlite.capture_committed
memory.postgresql.capture_committed
```

记录场景、类型、技术 ID、owner 哈希、状态、来源数量、四类准入数量和结果数量，
不记录 turn、候选、记忆内容与来源表达。敏感拦截只记录不含正文的类别原因码。

### 7.6 数据库运维

```text
memory.sqlite.migration.started
memory.sqlite.migration.applied
memory.sqlite.migration.skipped
memory.sqlite.migration.completed
memory.sqlite.health_check.completed
memory.postgresql.migration.started
memory.postgresql.migration.applied
memory.postgresql.health_check.completed
```

## 8. 如何查看详细日志

运行阶段一或阶段二演示：

```powershell
$env:LOG_LEVEL = "DEBUG"
uv run python examples/memory_phase_one.py
uv run python examples/memory_phase_two.py
```

查看最近 50 行：

```powershell
Get-Content .agent-lab/logs/agent-lab.log -Tail 50
```

持续观察：

```powershell
Get-Content .agent-lab/logs/agent-lab.log -Wait
```

按事件筛选：

```powershell
Select-String -Path .agent-lab/logs/agent-lab.log -Pattern "memory.create"
```

## 9. 新模块如何记录事件

```python
import logging
from time import perf_counter

from agent_lab.observability import log_event

_LOGGER = logging.getLogger(__name__)


def execute() -> None:
    started_at = perf_counter()
    log_event(_LOGGER, logging.DEBUG, "module.execute.started")

    # 执行业务逻辑

    log_event(
        _LOGGER,
        logging.INFO,
        "module.execute.completed",
        duration_ms=round((perf_counter() - started_at) * 1000, 3),
    )
```

事件名称建议采用：

```text
领域.对象.动作
```

例如：

```text
memory.create.completed
knowledge.index.completed
agent.run.completed
```

事件名称和字段名应保持稳定，因为后续测试、筛选和指标统计可能依赖它们。

## 10. 当前边界

当前是单进程文本日志，不包含：

- 集中式日志平台；
- trace/span 分布式追踪；
- metrics 指标服务；
- HTTP request id；
- 日志远程上传；
- 自动告警。

如果后续引入 Web 服务或后台 worker，应在保留现有事件名称的基础上增加
`request_ref`、`job_ref` 或 trace context，而不是把业务正文作为追踪字段。
