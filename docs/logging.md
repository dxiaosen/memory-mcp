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
| `owner_ref` | owner 引用（开发阶段为原值，上线前恢复为稳定假名） | `candidate_count` / `lexical_count` / `vector_count` / `recent_count` | 召回候选计数 |
| `client_ref` / `agent_ref` | 调用方稳定假名引用 | `expired_memory_count` / `expired_review_count` | 维护状态转换计数 |
| `capture_id` / `memory_id` / `revision_id` | 技术记录 ID | `result_count` / `duration_ms` | 结果数量 / 操作耗时 |
| `recall_ref` | 召回稳定关联标识（仅日志，不改 MCP 返回契约） | `auto_saved_count` / `pending_count` / `discarded_count` / `blocked_count` | 准入四类计数 |
| `status` / `error_code` | 稳定状态 | `error_type` | 异常类型名 |
| `error_message` | 异常消息（开发阶段记录，上线前恢复为仅 `error_type`） | `cause_type` / `cause_message` | 被 `raise ... from` 包装的原始异常类型与消息 |

`stable_reference()` 在开发阶段直接返回原值，便于在日志中识别是哪个 owner/team；
上线前需恢复为截断 SHA-256（12 位十六进制），避免直接输出 identifier。无论哪种模式
都不是匿名化机制，低熵 identifier 仍可能被枚举，日志访问权限仍需受控。

## 3. 两种日志模式

> **当前为开发阶段，已临时放开**：默认模式下也记录异常消息（`error_message`）与
> 被包装异常的 cause 链，正文字段（`query`/`content`/`source_expression` 等）不再自动
> 脱敏，以便排障。上线前需恢复完整脱敏集（见 §9 收尾步骤）。

| | 默认 `false`（开发阶段已放开） | 内容 `true` |
| --- | --- | --- |
| 运行元数据（阶段、引用、数量、状态、错误码、耗时） | ✅ | ✅ |
| 异常类型 `error_type` 与消息 `error_message`、cause 链 | ✅（开发阶段） | ✅ |
| SensitiveGuard 脱敏后的本轮输入和 subject hint | ❌ | ✅ |
| 通过敏感检查的候选及 source expression | ❌ | ✅ |
| 准入结果、持久化 memory/review/evidence 结构 | ❌ | ✅ |
| 当前 owner 范围内的召回查询、排序记录和输出内容 | ❌ | ✅ |
| Bearer Token / DSN / 密码 / API Key / 敏感规则命中的原文 | ❌ | ❌ |

自动遮盖与边界约束：

| 机制 | 说明 |
| --- | --- |
| `log_event()` 自动遮盖 | 凭据字段 `api_key`/`password`/`secret`/`token` 及 secret 后缀（最后防线）；开发阶段正文/异常消息字段不脱敏，上线前需恢复 `query`/`prompt`/`answer`/`content`/`source_expression` |
| 调用方约束 | 不能把完整对象塞进 `payload`/`details` 等未受保护字段 |
| backend 异常 | 开发阶段记录 `error_type` + `error_message` + cause 链以便排障；上线前恢复为仅 `error_type`，且 backend 异常消息不得含可还原 Secret |
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
- `owner_ref`：owner 引用（开发阶段为原值，上线前恢复为稳定假名），跨所有层关联。

## 5. 事件表

### 服务与 MCP

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.mcp.server.starting` | INFO | `host`, `port`, `mcp_path` |
| `memory.mcp.server.stopped` | INFO | `reason` |
| `memory.mcp.tool.started` | INFO | `request_id`, `client_ref`, `owner_ref`, `tool_name` |
| `memory.mcp.tool.completed` | INFO | `request_id`, `duration_ms`, `status`, `result_count`, `tool_name` |
| `memory.mcp.tool.failed` | ERROR/WARNING | `request_id`, `error_code`, `error_type`, `error_message`, `cause_type`, `cause_message`, `tool_name` |

### Capture 阶段

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.capture.started` | INFO | `capture_id`, `owner_ref`, `profile_id`, `profile_version`, `was_reprocessed`, `event_id`, `message_count`, `input_character_count` |
| `memory.capture.replay` | INFO | `capture_id`, `owner_ref`, `status`, `replayed` |
| `memory.capture.idempotency_conflict` | WARNING | `capture_id`, `owner_ref`, `event_id` |
| `memory.capture.completed` | INFO | `capture_id`, `owner_ref`, `profile_id`, `replayed`, `was_reprocessed`, `duration_ms`, `extracted_candidate_count`, `outcome_count`, `candidate_count`, `auto_saved_count`, `pending_count`, `discarded_count`, `blocked_count`, `reason_counts`, `duplicate_count`, `replacement_count`, `review_count`, `relation_proposal_count`, `relation_accepted_count`, `relation_skipped_count`, `failure_code`, `candidate_extraction_duration_ms`, `candidate_validation_duration_ms`, `admission_duration_ms`, `lifecycle_duration_ms`, `relation_duration_ms`, `persistence_duration_ms` |
| `memory.capture.incomplete` | WARNING | `capture_id`, `owner_ref`, `profile_id`, `status`, `failure_code`, `was_reprocessed`, `duration_ms` |
| `memory.capture.processing_failed` | ERROR | `capture_id`, `error_type`, `error_message`, `cause_type`, `cause_message`, `owner_ref` |
| `memory.capture.invalid_output` | WARNING | `capture_id`, `error_type`, `error_message`, `error_detail`（开发期：优先读 `InvalidModelOutputError.context` 结构化违规信息，其次 pydantic `ValidationError` 经 `__cause__` 链解析的「字段: 原因」摘要，最后异常消息兜底；保证非 null）, `cause_type`, `cause_message`, `owner_ref` |
| `memory.capture.extraction_attempt.started` | INFO | `capture_id`, `attempt`, `max_attempts`（结构化抽取每次尝试开始；recommend.md §3） |
| `memory.capture.extraction_attempt.failed` | WARNING | `capture_id`, `attempt`, `max_attempts`, `duration_ms`, `error_type`, `error_message`, `retryable`（仅对 `InvalidModelOutputError` 等 recoverable 结构错误重试；业务校验 `invalid_source_expression` 不重试） |
| `memory.capture.extraction_attempt.completed` | INFO | `capture_id`, `attempt`, `max_attempts`, `duration_ms`（某次尝试成功，capture 继续后续候选处理） |
| `memory.capture.relation_extraction_attempt.started` | INFO | `capture_id`, `attempt`, `max_attempts`（关系抽取每次尝试开始；recommend.md §1） |
| `memory.capture.relation_extraction_attempt.failed` | WARNING | `capture_id`, `attempt`, `max_attempts`, `duration_ms`, `error_type`, `error_message`, `retryable`（仅 **fatal** 拒绝才重试：invalid_source_expression/relation_endpoint_outside_catalog/模型结构错误；全部 attempt fatal 才 incomplete。`error_message` 形如 `relation validation failed: <reason_code>`，与 reason 一致） |
| `memory.capture.relation_extraction_attempt.completed` | INFO | `capture_id`, `attempt`, `max_attempts`, `duration_ms`（某次关系抽取尝试无 fatal rejected proposal 即完成；non-fatal skip 不触发 retry，Capture 继续） |
| `memory.capture.relations_planned` | INFO | `capture_id`, 模型/prompt/schema 版本, endpoint/proposal/accepted/skipped 数量 |
| `memory.capture.candidate.assertion_normalized` | DEBUG | `candidate_ref`, `memory_type`, `source_role`, `source_type`, `from_assertion_kind`, `to_assertion_kind`（模型自报 assertion_kind 与可信来源语义冲突时纠正：assistant+user_* → system_inference；tool/document/web source_type + 任意非 external → external_fact） |
| `memory.capture.candidates_truncated` | DEBUG | `model_id`, `original_count`, `kept_count`, `soft_limit`（解析出的候选数超过软上限 12 时按 confidence 降序裁剪） |

Capture 内容模式事件（仅 `LOG_CONTENT=true`）：

| 事件名 | 记录内容 |
| --- | --- |
| `memory.capture.input` | 脱敏输入、messages、subject_hint |
| `memory.capture.candidates` | 候选及 source expression |
| `memory.capture.admission` | 准入结果 |
| `memory.capture.validation` | `extracted_candidate_count`, `validated_candidate_count`, `rejected`（被 invalid_source_expression/ambiguous_source_message 提前拒绝的 proposal 完整字段） |
| `memory.capture.structured_output.invalid` | `model_id`, `prompt_version`, `schema_version`, `raw_type`, `raw_preview`（截断的原始响应，开发态 content log）, `error_type`, `error_message`（结构化输出校验失败时记录，便于定位 None / schema malformed / 重复 wrapper；recommend.md §3） |
| `memory.capture.relation_candidates` | 脱敏建议和计划关系 |
| `memory.capture.relation_validation_rejected` | 被拒/跳过的关系建议（`source_memory_id`/`target_memory_id`/`relation_type`/`confidence`/`source_expression`/`reason_code`）。**fatal**（retry / 可使 Capture 失败）：invalid_source_expression/relation_endpoint_outside_catalog；**non-fatal**（skip，不 retry、不拖垮 Capture）：relation_policy_mismatch/relation_not_explicit/relation_low_confidence/relation_insufficient_evidence/relation_negated/relation_reversed_direction/relation_duplicate/relation_non_user_source。recommend.md §2/§4 |
| `memory.capture.persisted` | 持久化结构（memory/review/duplicate/replacement/relation） |

### Recall 阶段

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.recall.started` | INFO | `recall_ref`, `owner_ref`, `profile_id`, `embedding_enabled`, `max_items`, `token_budget` |
| `memory.recall.candidates` | INFO | `recall_ref`, `candidate_count`, `candidate_limit`, `lexical_count`, `vector_count`, `recent_count`, `profile_id`, `embedding_degraded`（召回流程 started → candidates → ranked → output → completed 的中间环节，便于区分"未召回"还是"召回了但被阈值过滤"） |
| `memory.recall.embedding_failed` | WARNING | `error_type`, `error_message` |
| `memory.recall.completed` | INFO | `recall_ref`, `owner_ref`, `profile_id`, `duration_ms`, `result_count`, `estimated_tokens`, `token_budget`, `truncated`, `zero_result`, `candidate_count`, `lexical_count`, `vector_count`, `recent_count`, `threshold_passed_count`, `relation_boosted_count`, `embedding_enabled`, `embedding_degraded`, `query_embedding_duration_ms`, `repository_candidate_duration_ms`, `ranking_duration_ms`, `evidence_loading_duration_ms`, `render_duration_ms`, `accounted_duration_ms`（各 stage 耗时之和）, `unaccounted_duration_ms`（总耗时 - accounted，定位连接/序列化等缺口，§8）（分阶段耗时，未执行的阶段记 0；便于定位 recall 慢在 embedding/DB/排序/evidence 渲染） |
| `memory.recall.query_skipped_operational` | INFO | `recall_ref`, `owner_ref`, `profile_id`（operational-only 查询跳过 semantic recall，不调 embedding、返回空；recommend.md §5） |
| `memory.recall.timeline.started` | INFO | `recall_ref`, `owner_ref`, `profile_id`, `focus_memory_id`, `max_hops`, `token_budget` |
| `memory.recall.timeline.completed` | INFO | `recall_ref`, `owner_ref`, `profile_id`, `hop_count`, `estimated_tokens`, `token_budget`, `truncated` |

Recall 内容模式事件（仅 `LOG_CONTENT=true`）：

| 事件名 | 记录内容 |
| --- | --- |
| `memory.recall.input` | 脱敏查询、`normalized_query`（剔除操作/工具/格式指令子句后的查询，recommend.md §7）、subject、task_intent |
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
| `memory.review.confirm_failed` | WARNING | `owner_ref`, `review_id`, `error_type`, `error_message` |
| `memory.maintenance.completed` / `.failed` | INFO/ERROR | `duration_ms`, 状态转换计数, `expired_relation_context_count`, `reminder_count`, `has_more` / `error_type`, `error_message` |
| `memory.maintenance.reminder_written` | INFO | `owner_ref`, `profile_id`, `relation_type`, `focus_memory_id`, `reminder_memory_type` |
| `memory.maintenance.reminder_skipped` | WARNING | `error_type`, `error_message`, `profile_id`, `relation_type`, `focus_memory_id` |
| `memory.team_extraction.completed` / `.batch_completed` / `.failed` / `.batch_started` / `.team_failed` | INFO/ERROR | `team_owner_ref`, `member/memory/cluster/candidate_count`, `duration_ms`, `team_count` / `error_type`, `error_message` |
| `memory.embedding.completed` | DEBUG | — |
| `memory.embedding.computation_failed` | WARNING | `error_type`, `error_message` |
| `memory.embedding.batch_failed` | WARNING | `attempt`, `max_attempts`, `batch_size`, `error_type`, `error_message`, `model_id` |
| `memory.embedding.provider_disabled` | WARNING | `error_type`, `error_message`, `reason`（`embedding_settings_invalid` / `provider_construction_failed`） |

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
| `memory.postgresql.migration.started` / `.applied` / `.rebuild` / `.failed` | INFO/ERROR | `version` / `error_type`, `error_message`（`.failed` rollback 后记录再 raise） |
| `memory.postgresql.health_check.completed` | INFO | `status` |
| `memory.postgresql.health_check.failed` | ERROR | `error_type`, `error_message` |
| `memory.postgresql.pool_opened` | INFO | `min_size`, `max_size` |
| `memory.postgresql.pool_open_failed` | ERROR | `error_type`, `error_message` |

### 认证

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `memory.auth.owner_key_derivation_failed` | WARNING | `error_type`, `error_message`（记后以 `from None` 抑制链并转 `UnauthenticatedError`） |
| `memory.auth.team_owner_key_derivation_failed` | WARNING | `error_type`, `error_message`（同上，逐 team_id 记录） |

### Agent Host

| 事件名 | 级别 | 字段 |
| --- | --- | --- |
| `agent_hook.started` | INFO | `run_ref` |
| `agent_hook.recall.completed` | INFO | `run_ref`, `recalled_count`, `status` |
| `agent_hook.recall.fail_open` | WARNING | `error_code`, `retryable`, `error_type`, `error_message`, `cause_type`, `cause_message` |
| `agent_hook.capture.completed` / `.skipped` | INFO | `run_ref`, `status` / `reason` |
| `agent_hook.capture.attempt.started` | INFO | `event_ref`, `attempt`, `timeout_seconds` |
| `agent_hook.capture.attempt.completed` | INFO | `event_ref`, `attempt`, `duration_ms`, `replayed`, `status` |
| `agent_hook.capture.attempt.failed` | WARNING | `event_ref`, `attempt`, `duration_ms`, `error_type`, `error_code`, `retryable` |
| `agent_hook.capture.retry` | WARNING | `attempt`, `error_code`, `retryable`, `error_type`, `error_message`, `cause_type`, `cause_message` |
| `agent_hook.capture.exhausted` | WARNING | `attempt`, `error_code`, `retryable`, `error_type`, `error_message` |
| `agent_hook.capture.fail_open` | WARNING | `attempts`, `error_code`, `error_type`, `error_message` |
| `agent_hook.pending_retry.completed` | INFO | `run_ref`, `attempts`, `status`, `warning_code` |
| `agent_hook.pending_retry.failed` | ERROR | `run_ref`, `error_type`, `error_message`, `cause_type`, `cause_message` |
| `agent_hook.transcript.parse_failed` | WARNING | `transcript_path`, `error_type`, `error_message`（Claude Code transcript JSONL 不可读或结构非法时 best-effort 跳过，不阻断 capture） |
| `agent_hook.transcript.document_messages_extracted` | DEBUG | `transcript_path`, `document_message_count`（从 transcript 解析出文件读取来源消息数） |
| `agent_hook.failed` | ERROR | `error_code`, `hook_event`, `error_type`, `error_message`, `error_detail`（pydantic ValidationError 的「字段: 原因」摘要）, `error_cause_type`, `error_cause_message`, `encoding`/`byte_position`（仅 `stdin_decode_error`）, `raw_head`/`raw_len`（stdin 解析失败时的原始输入前缀） |
| `turn_state.read_failed` / `.invalid` | WARNING | `path`, `error_type`, `error_message` |
| `turn_state.cleanup_corrupt` | WARNING | `path`, `error_type`, `error_message` |
| `mcp_client.http_status_error` | WARNING | `tool`, `status_code`, `error_type`, `error_message` |
| `mcp_client.http_error` | WARNING | `tool`, `error_type`, `error_message` |
| `mcp_client.unexpected_error` | ERROR | `tool`, `error_type`, `error_message` |
| `mcp_client.tool_error` | WARNING | `tool`, `error_code`, `retryable` |
| `mcp_client.session_close_failed` | DEBUG | `session_id`, `error_type`, `error_message` |
| `mcp_client.unsupported_response_type` / `.invalid_response_*` / `.response_id_mismatch` / `.protocol_error` / `.invalid_result_shape` | WARNING | `status_code`, 对应 shape/type/mismatch 字段 |
| `mcp_client.invalid_initialize_response` | WARNING | `protocol_version` |

Agent Hook 事件默认记录 prompt/最终回复等内容字段以便排障；`api_key`/`password`/`secret`/`token` 仍脱敏。

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
# started → try 业务逻辑 → except: log_event(..., error_type=type(exc).__name__,
#   error_message=str(exc), cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None,
#   cause_message=str(exc.__cause__) if exc.__cause__ else None) → raise
# → log_event(..., duration_ms=round((perf_counter()-started_at)*1000, 3))
```

| 规则 | 说明 |
| --- | --- |
| 事件名 | 使用 `领域.对象.动作`；已发布事件名和字段名保持稳定 |
| 内容日志 | 必须用 `log_content_event()`，确保数据已通过身份限定和敏感检查 |
| 禁止传入 | settings、HTTP headers、数据库连接参数、未经 SensitiveGuard 检查的原始 payload |
| 失败路径 | 每个 `except` 必须记 `error_type` + `error_message`；被包装异常经 `raise ... from exc` 时，在边界层补 `cause_type`/`cause_message` 与 pydantic `error_detail`（field: reason）；不可静默吞异常 |
| Agent 包 | 从 `memory_mcp_agent.logging` 导入 `log_event`，不得反向依赖 `memory_mcp.logging` |
| 同一错误 | 只在最有业务上下文的一层记录一次（如模型输出校验失败只在 `capture_service` 边界记一次，不在 `backends`/`structured_model` 再记） |

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
| 2. 恢复脱敏集 | 在 `server`/`agent` 的 `logging.py` 把 `query`/`prompt`/`answer`/`content`/`source_expression` 加回 `_SENSITIVE_FIELD_NAMES`；同步恢复 CLAUDE.md 铁律为仅记 `error_type` |
| 3. 重启并确认 | 重启服务并确认 `logging.configured` 的 `content_logging=false`、异常消息字段不再出现 |
| 4. 处理内容日志 | 按数据管理要求删除或归档内容日志 |
| 5. 处理 Secret 泄漏 | 开发阶段日志可能含 backend 异常消息，若其中夹带 Secret，停止服务并轮换对应凭据，不能只删除文件 |

内容日志是诊断能力，不是记忆存储、审计账本或长期业务数据出口。
