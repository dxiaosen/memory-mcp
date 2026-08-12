## Why

2026-08-11 联调发现 capture 调用链同步执行结构化抽取（LLM），Stop hook 端到端耗时 ~33s。
Phase 1 改"模型自主调用 capture"以避开阻塞，但 PoC 暴露：模型自主判断不可靠——该记的 turn
（"这是我对青禾食品的长期研究判断…"）不调 capture，漏捕获。主流记忆产品（mem0/Letta/
腾讯 TencentDB-Agent-Memory）都是 **hook 强制捕获 + 异步抽取**：登记毫秒级落库，
抽取异步后台进行，触发不依赖模型判断。

根因不是"模型不该调用"，而是"调用即同步抽取"的耦合 + "触发依赖模型判断"的不可靠。
本变更同时解两件事：服务端队列解耦"登记/抽取"，恢复 hook 强制触发。

## What Changes

把 capture 拆成两段 + 恢复 hook 强制触发：

1. **入队（同步、毫秒级）**：`capture_completed_turn` 调 `enqueue_capture`——校验 + 幂等检查
   + 敏感脱敏 + 写 `PENDING` 行（含脱敏后 content/subject_hint）→ 立即回执 `status=pending`。
   Agent 主循环不阻塞。
2. **worker 异步抽取（同进程 asyncio loop）**：新增 `_run_capture_reprocess_loop`，复用
   maintenance loop 模式（ASGI lifespan 起 task、`has_more` 续批、退避）。每轮 `list_pending_captures`
   （`FOR UPDATE SKIP LOCKED` 并发安全）→ 逐条 `_capture_turn_extract` → `commit_capture`
   覆盖终态（COMPLETED / REPROCESS_REQUIRED / FAILED）。
3. **恢复 hook 强制触发**：Agent Stop hook（`hosts.py` `_after`）从 Phase 1 的 no-op 改回
   入队 capture——BeforeRun 写本地 TurnState（prompt 暂存，因 Stop 事件不带 user_input）+
   召回注入，AfterRun 取 prompt 后调 `bridge.after_run_success` → `client.capture_completed_turn`
   （简化契约）入队。恢复 `transcript.py`（inspect-skip 判定：assistant 调 memory 管理工具
   的 turn 跳过入队）。入队失败走 fail-open（不入 outbox 不重投，下轮 Stop 幂等兜底）。

新增 `CaptureStatus.PENDING`（"已入队待抽取"，不违反 failure_code 约束；REPROCESS_REQUIRED
专留"抽取失败重试"）。`memory_captures` 加 `content` / `subject_hint` 列存脱敏原文，单表无 JOIN。
开关 `capture_enqueue_enabled`（默认 true）可灰度回退同步抽取。保留 Phase 1 简化契约
（服务端派生 event_id/observed_at/contract_version）。模型仍可显式调 `capture_completed_turn`
（与 hook 幂等，补充用）。
