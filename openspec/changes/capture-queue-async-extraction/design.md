## Context

Phase 1（`model-autonomous-capture`）去 Stop hook 强制捕获、简化 capture 契约（服务端组装
event_id/observed_at/contract_version/payload_fingerprint），保留服务端二次抽取。本变更在其之上
加服务端队列，解决"调用即抽取"的同步阻塞。

## Decision

- **新增 `CaptureStatus.PENDING`** 而非复用 `REPROCESS_REQUIRED`：语义不同——PENDING 是
  "已入队从未抽取"，REPROCESS_REQUIRED 是"抽取失败待重试"。复用会让 failure_code 约束
  （REPROCESS_REQUIRED 必有 failure_code）与"未抽取无 failure_code"冲突。
- **`memory_captures` 加 `content`/`subject_hint` 列**：worker 重建 `TurnEnvelope` 需要原文，
  单表存储避免 JOIN `memory_capture_outcomes`（outcome 在抽取后才写）。content 在入队前经
  `sensitive_guard.inspect` 脱敏，worker 读到的就是已脱敏原文。
- **同进程 asyncio task worker**：复制 maintenance loop 模式（`_run_maintenance_loop`），
  不引入外部 MQ / Celery / Redis——单进程内存队列足够，PG 行锁防并发重复抽取。
- **`FOR UPDATE SKIP LOCKED`**：多 worker 实例并发捞 pending 行时互不抢同一条。
- **`capture_enqueue_enabled` 开关**：默认 true 入队异步；false 回退同步抽取（灰度/回退/测试）。
- **worker 重建 `PrincipalContext` 只带 `owner_id`**：`team_owner_ids` 来自 JWT claims，单行
  capture 不携带；团队提取是独立循环，auto-relations 在 owner 存储内工作。

## Risk / Trade-off

- worker 失败无重试上限——靠 `has_more` 退避，REPROCESS_REQUIRED 行会被下一轮 worker 重试。
  后续可加 `retry_count` 列与上限。
- 本变更不解决"模型拿不到稳定 conversation_id/turn_id"的 Phase 1 触发缺口——队列只解决
  "Stop hook 不等"，capture 仍由模型自主调用。
