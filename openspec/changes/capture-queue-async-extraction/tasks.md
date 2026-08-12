# Tasks

## Domain

- [x] `core/domain/capture.py` `CaptureStatus` 加 `PENDING = "pending"`
- [x] `CaptureResult.__post_init__` 扩展：PENDING 不允许 failure_code / outcomes
- [x] 新增 `CaptureReprocessResult` dataclass（processed/completed/reprocess_required/failed/has_more）
- [x] `core/domain/__init__.py` 导出 `CaptureReprocessResult`

## Schema

- [x] `0001_memory_schema.sql` `memory_captures` 加 `content TEXT NOT NULL` / `subject_hint TEXT`
- [x] `memory_captures_content_non_empty` CHECK
- [x] `memory_captures_status` CHECK 加 `'pending'`
- [x] `memory_captures_failure_state` CHECK 扩展：pending 允许 failure_code NULL
- [x] `memory_captures_pending_idx` 索引（WHERE status='pending'）
- [x] `schema.py` `_REQUIRED_INDEXES` 加新索引名
- [x] `docs/testing.md` 索引数 6→7

## Repository

- [x] `core/ports/repositories.py` 加 `CaptureEnqueueWrite` / `PendingCapture` dataclass
- [x] `core/ports/repositories.py` 加 `commit_capture_enqueue` / `list_pending_captures` Protocol 方法
- [x] `core/ports/__init__.py` 导出
- [x] PostgreSQL `repository.py`：`commit_capture_enqueue`（advisory lock + 幂等 replay + INSERT pending）
- [x] PostgreSQL `repository.py`：`list_pending_captures`（FOR UPDATE SKIP LOCKED）
- [x] PostgreSQL `repository.py`：`commit_capture` existing 检查加 PENDING 走重写路径
- [x] PostgreSQL `_insert_capture_run` 加 content/subject_hint 列
- [x] InMemory `repository.py`：同构实现 + `_captures` tuple 存储 + PENDING 重写

## Application

- [x] `capture_service.py` `enqueue_capture`：校验 + 幂等 + guard.inspect + commit_capture_enqueue
- [x] `capture_service.py` `run_capture_reprocess`：list_pending → `_pending_to_turn` → `_capture_turn_locked` → commit
- [x] `capture_service.py` `_capture_turn_locked` 幂等检查加 PENDING 走抽取路径
- [x] `service.py` 暴露 `enqueue_capture` / `run_capture_reprocess` 门面
- [x] `core/__init__.py` 导出 `CaptureReprocessResult`

## app.py worker

- [x] `MemoryMcpServer.__init__` 加 `run_capture_reprocess` / `capture_reprocess_interval_seconds`
- [x] `capture_reprocess_health` 属性 + `MaintenanceHealth` 复用（observe_success 接 CaptureReprocessResult）
- [x] ASGI lifespan 起第三个 task `_run_capture_reprocess_loop`
- [x] `_run_capture_reprocess_loop`（同 maintenance loop 模式 + 专用续批软上限 16 / 退避 1s）
- [x] `create_memory_mcp_server` 接线
- [x] health endpoint snapshot 加 `capture_reprocess`

## 配置

- [x] `settings.py` `capture_reprocess_interval_seconds`（默认 5）+ `capture_enqueue_enabled`（默认 true）
- [x] `tools/capture.py` 按 `capture_enqueue_enabled` 切换 enqueue_capture / capture_turn
- [x] `.env.example` 加两项
- [x] `docs/config.md` 加两项 + worker 说明

## 日志

- [x] `docs/logging.md` 加 `memory.capture.enqueued` / `memory.capture.reprocess.completed` / `memory.capture.reprocess.failed`

## 测试

- [x] `tests/unit/test_capture_queue.py`：enqueue + PENDING + worker reprocess + 幂等 + has_more + 失败终态
- [x] `tests/contract/test_postgresql_contract.py`：required-methods 集加 `commit_capture_enqueue` / `list_pending_captures`
- [x] `tests/integration/test_server_transport.py`：`_running_server` 暴露 service + `capture_enqueue_enabled`
- [x] 集成测试 `test_capture_enqueue_returns_pending_then_worker_completes`：pending 回执 + worker 后 recall 命中
- [x] 既有 capture 契约测试默认 `capture_enqueue_enabled=False` 保持同步抽取语义

## 门禁

- [x] `uv run ruff check .` 通过
- [x] `uv run pytest -q` 337 passed / 13 skipped（真实 PG 契约跳过）
- [x] `tests/contract/test_dependency_boundaries.py` 通过（core 不破）

## Agent（恢复 hook 强制触发）

- [x] `transcript.py` 恢复（`extract_document_messages` + `collect_turn_tool_uses`，inspect-skip 判定）
- [x] `state.py` 加回轻量 `TurnState`（session_id/turn_id/prompt，无 capture payload）+ `save`/`load`/`delete`
- [x] `client.py` 加回 `capture_completed_turn`（简化契约：只传 conversation_id/turn_id/user_input/final_output + 可选 profile_id/subject_hint）+ `CaptureResponse`/`CaptureSummary`（status 含 pending）
- [x] `bridge.py` 加 `AfterRunResult` + `after_run_success`（精简版：无重试/无去重缓存，fail-open 降级）
- [x] `settings.py` 加 `capture_timeout_seconds`（默认 5）
- [x] `hosts.py` `_after` 改回入队 capture（恢复 `_is_inspect_or_manage_turn` + `_skip_after`，删 `_noop_after`）
- [x] `hosts.py` `_before` 加回 `save(TurnState)` 写 user_input
- [x] 文档：`docs/design.md` §10.2-§10.7 改"hook 强制入队 + 服务端队列异步抽取"；`CLAUDE.md` 测试边界改回 hook 触发；`docs/logging.md` agent_hook.capture.* 事件恢复
- [x] 测试：`tests/integration/test_agent_hosts.py` 更新（入队断言 + inspect-skip + fail-open）；`tests/integration/test_server_transport.py` hook adapter 测试改回 Stop 入队 + worker 抽取
- [x] 门禁：ruff + pytest + pyright 通过
