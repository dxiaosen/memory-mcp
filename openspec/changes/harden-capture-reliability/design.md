## Context

真实联调日志（2026-08-07）显示：

- 15:12:28 的材料阅读 capture 真实耗时 33 秒，但 Agent 在 15:12:44（+16s）与 15:12:59（+15s）两次并发重发同一 `event_id`，服务端对后两次返回 `replay`。根因是 `MemoryHookSettings.timeout_seconds=15.0` 同时用于 recall 与 capture，httpx ReadTimeout 被 [client.py:238](agent/src/memory_mcp_agent/client.py#L238) 映射为 `memory_mcp_unavailable(retryable=True)`，触发 [bridge.py:228](agent/src/memory_mcp_agent/bridge.py#L228) 的有界重试——在前一次请求仍 in-flight 时并发发下一次。
- 15:07:14 的"研究基准"输入整轮失败：`memory.capture.invalid_output / error_detail=null / candidate_count=0`。根因是 [candidate_processing.py:301](server/src/memory_mcp/core/application/candidate_processing.py#L301) 对单条 `source_expression not in redacted_source` 直接 `raise InvalidModelOutputError`，且该异常无 `__cause__`，[_validation_errors](server/src/memory_mcp/core/application/capture_service.py#L593) 只从 cause 链翻 pydantic errors，故 error_detail 恒为 null。

## Goals / Non-Goals

**Goals:**

- 正常 30–40 秒 capture 不触发重试：`CallToolRequest=1 / capture.completed=1 / replay=0`。
- 单条 `source_expression` 不匹配只丢弃该条，不拖垮整轮；`discarded_count` 可见。
- `memory.capture.invalid_output` 在仍走该路径的失败上不出现 `error_detail=null`。
- Agent 日志能直接回答"第几次 attempt、是否 replayed、超时多少"。

**Non-Goals:**

- 不改 Admission 阈值或四类 decision（recommend.md §3.3 明确不应改）。
- 不改 Server 的 replay / 幂等行为（已正确）。
- 不引入外部队列或常驻重试 worker。
- 不恢复生产级脱敏（recommend.md §0：开发阶段主动放开，本轮不整改）。

## Decisions

### D1: 双超时而非单超时

recall 与 capture 的延迟特征不同：recall 需在用户请求开始前返回（10–15 秒上限），capture 需覆盖模型抽取 + DB（60–75 秒）。单一超时无法兼顾。拆为 `recall_timeout_seconds=15.0`、`capture_timeout_seconds=70.0`，关系满足 `Claude Stop Hook 90s > Agent capture 70s > Server P95 <60s`。

### D2: per-request timeout 而非双 httpx client

httpx 支持请求级 `timeout=` 覆盖 client 级。保留单 `_ensure_http_client`，给 `_call_tool` 增 `timeout: float | None` 形参，recall/capture 各传自己的值。避免双 client 的连接池/生命周期复杂度。

### D3: source_expression 降级为 DISCARD 而非保留 raise

`AdmissionDecision.DISCARD` 与 `discarded_count` 已存在（[capture_service.py:481](server/src/memory_mcp/core/application/capture_service.py#L481)）。降级为 `outcomes.append(CaptureOutcome(..., DISCARD, "invalid_source_expression")) + continue` 让其余候选正常处理。reason_code 进 `reason_counts`，可观测。不再走 `invalid_output`（该路径的 error_detail 问题随之消失）。

### D4: InvalidModelOutputError 增可选 context

仍走 `invalid_output` 的路径（confidence 越界、非对象、UUID 非法等）需要诊断。给 `InvalidModelOutputError` 增 `context: dict[str, Any] | None = None`，各 raise 处带上违规值；`_validation_errors` 优先读 context，其次走 cause 链。不破坏现有异常语义（可选字段）。

### D5: attempt 事件在 bridge for 循环体记

[bridge.py:228](agent/src/memory_mcp_agent/bridge.py#L228) 的 `for attempt in range(...)` 循环首尾 + except 已有 `agent_hook.capture.retry/.exhausted`。补 `attempt.started`（进循环）、`.completed`（成功返回）、`.failed`（except）三事件，带 `event_ref/attempt/timeout_seconds/duration_ms/replayed/status`。与现有 retry/exhausted 互补，不冲突。

## Risks / Trade-offs

- capture 70 秒超时意味着真实故障时单次失败要等 70 秒才重试。可接受：Stop Hook 外层 90 秒仍覆盖；且真实故障（网络断、Server 崩）通常快速失败而非慢超时。
- source_expression 降级为 DISCARD 后，模型若系统性编造 source_expression，候选会被静默丢弃。缓解：`discarded_count > 0` 在 completed 事件可见，且 `reason_counts` 含 `invalid_source_expression`，可触发告警。
- InvalidModelOutputError 增 context 可能携带正文（开发阶段可接受，recommend.md §0 已放开；上线前随脱敏收口一并处理）。
