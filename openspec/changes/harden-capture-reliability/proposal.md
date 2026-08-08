## Why

2026-08-07 真实联调日志暴露三个 P0 级捕获可靠性问题：

1. Agent 单一 HTTP 超时 `timeout_seconds=15.0` 同时用于 recall 与 capture，而真实 capture 处理耗时约 33 秒。Agent 在第一次请求仍在 in-flight 时因 httpx ReadTimeout 触发可重试错误，并发提交同一 `event_id` 两到三次，服务端最终对后两次返回 `replay`。Server 幂等正确，但 Agent 产生重复网络请求、污染日志、增加 Server 压力。
2. 候选处理在单条 `source_expression` 不匹配脱敏后原文时，对整轮 `raise InvalidModelOutputError`，导致用户最重要的"研究基准"输入整轮 0 候选、整轮失败。一条坏候选拖垮整轮，且因 `InvalidModelOutputError` 直接 `raise`（无 `__cause__` 链），`memory.capture.invalid_output` 的 `error_detail` 恒为 null，开发阶段无法定位。
3. `memory.capture.invalid_output` 事件在直接 raise 的路径上 `error_detail=null`，违反 logging.md 承诺的"字段:原因"摘要，开发阶段排障无依据。

这些问题在功能已跑通后直接影响真实投研使用的记忆完整性、可观测性和演示稳定性，应在扩展业务能力前收口。

## What Changes

- Agent 拆分 recall 与 capture 的 HTTP 超时：recall 保持 15 秒（不阻塞用户请求开始），capture 提升到 70 秒（覆盖真实结构化抽取 + DB 延迟）。保留 `capture_max_attempts=3` 与退避，但 capture 超时 70 秒使重试只在真实故障时触发，而非正常处理时间。
- 新增 Agent 调试日志 `agent_hook.capture.attempt.started/.completed/.failed`，显式记录第几次 attempt、超时秒数、replayed、status，使重复请求可从日志直接定位而非靠时间推断。
- 候选处理把单条 `source_expression` 不匹配从"整轮 `raise`"降级为"丢弃该条候选 + 记 `reason_code=invalid_source_expression`"，`discarded_count` 自动统计；其余候选正常处理。
- `memory.capture.invalid_output` 在仍走该路径的非法输出（confidence 越界、非对象、UUID 非法等）上补记违规字段（source_expression/candidate_index/candidate_subject），开发阶段暴露具体失败值。
- 同步 logging.md 事件表与 unit 日志测试。

## Capabilities

### New Capabilities

- `capture-reliability`: 规定 Agent 的双超时与有界重试、候选级 source_expression 失败降级、以及 invalid_output 的开发期诊断字段。

### Modified Capabilities

无。本变更以独立增量能力描述新增行为；后续视归档时序同步到主规范。

## Impact

- Agent：`settings.py` 拆双超时；`client.py` 按工具传 per-request timeout；`bridge.py` 增 attempt 三事件。新增 `MEMORY_HOOK_RECALL_TIMEOUT_SECONDS` / `MEMORY_HOOK_CAPTURE_TIMEOUT_SECONDS` 环境变量。
- Core：`candidate_processing.py` source_expression 校验降级为 DISCARD；`capture_service.py` invalid_output 补字段；`exceptions.py` 给 `InvalidModelOutputError` 增可选 context。
- 文档：logging.md 事件表 + agent 事件；config.md Agent 超时项；agent/.env.example 注释。
- 测试：candidate_processing 降级单测；logging_events 增 attempt 事件与 invalid_output 字段断言。
- 不改 DB schema、不改 Admission 阈值、不改 Core 自包含不变量。
