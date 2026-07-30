# Memory MCP 阶段三验收记录

验收日期：2026-07-30

## 结论

阶段三已把阶段二的进程内 Memory Core 暴露为可由不同 Agent 接入的远程
MCP 服务，并建立了可信身份边界。旧 RAG 产品线已经从运行时代码、依赖和测试
中移除；未来真实抽取仍会复用的聊天模型工厂和结构化候选 adapter 继续保留。

当前服务可独立启动并提供健康检查、鉴权、记忆管理和 pending 审核。默认启动
入口暂不选择具体模型供应商，因此未注入 `CandidateExtractor` 时，
`capture_completed_turn` 会返回稳定的 `capture_not_configured`，而不是静默
丢弃或伪造结果。真实模型 backend 属于阶段六。

## 交付边界

- 固定 `mcp==1.29.0`，使用 Streamable HTTP `/mcp` 和公开 `/health`；
- `MEMORY_MCP_DEMO_TOKENS_JSON` 为空时拒绝启动；
- token 在服务端映射到 owner、tenant、subject、client、agent 和 scopes；
- 工具参数不接受 owner 字段，所有 Core 调用使用服务端生成的
  `PrincipalContext`；
- 完成 `capture_completed_turn`、`list_memories`、`get_memory`、
  `list_pending_reviews`、`confirm_pending_memory`、
  `reject_pending_memory` 六个工具；
- 完成版本化事件、payload fingerprint、稳定错误码、request id 和无正文日志；
- 相同 event/payload 可安全 replay，相同 event/不同 payload 返回 conflict；
- confirmed/rejected review 重试返回稳定结果；
- SQLite 重启后仍保持 event 幂等和用户隔离。

## 真实传输验收

自动化测试启动真实 Uvicorn ASGI 服务，通过官方 MCP Python Client 的
`streamable_http_client` 建立远程 session，并覆盖：

1. 无 token 请求返回 HTTP 401；
2. `tools/list` 只返回预期六个工具，输入 schema 为
   `additionalProperties=false`；
3. 调用方注入 `owner_id` 被协议层拒绝；
4. 一轮内容同时产生 1 个自动保存、1 个 pending、1 个敏感拦截；
5. 响应和 SQLite 文件都不包含测试中的敏感明文；
6. 同 event replay、不一致 payload conflict、未知 contract version
   安全失败；
7. 同 owner 的 Agent A 与 Agent B 共享记忆；
8. 另一 owner 列表为空，猜测 memory/review identifier 也只得到 unavailable；
9. 只有 `memory:read` 的 token 不能执行 capture；
10. 关闭并重开 SQLite 服务后 replay 不会再次调用 extractor 或重复写入。

另外使用官方 MCP Inspector 对真实 `/mcp` 地址执行 `tools/list`，结果为：

```text
capture_completed_turn
list_memories
get_memory
list_pending_reviews
confirm_pending_memory
reject_pending_memory
```

Inspector 进程正常退出，说明该入口不是仅在单元测试内部可调用的私有协议。

## 本机验证结果

```text
.venv\Scripts\python.exe -m pytest -q
52 passed in 14.30s

.venv\Scripts\python.exe -m ruff check src tests examples
All checks passed!
```

完整验证还包括：

```powershell
uv lock --offline
uv run ruff format --check .
openspec-cn validate add-general-memory-core --strict
```

## 启动与最小客户端

```powershell
Copy-Item .env.example .env
# 修改 .env 中的演示 token
uv run memory-mcp
```

另一个终端：

```powershell
uv run python examples/memory_mcp_client.py `
  --token <local-token> tools
uv run python examples/memory_mcp_client.py `
  --token <local-token> memories
```

## 明确限制

- 当前 JSON token 映射只服务于原型和现场演示，不等同于生产 OAuth；
- SQLite 组合根按单进程原型验收，暂不支持多 worker 写入拓扑；
- 阶段四之前没有 `recall_memory`，也没有 duplicate/replacement 合并；
- 阶段五之前 Hook SDK 和 Codex/第二 Agent adapter 尚未实现；
- 阶段六才把真实结构化模型 backend 接入默认服务入口，并保留固定离线 backend。

这些限制均由后续阶段覆盖，不影响阶段三“远程服务、可信身份、管理工具和
幂等捕获边界”的验收结论。
