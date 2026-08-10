## Why

2026-08-10 E2E 暴露 3 类剩余语义与边界问题：明确不确定/猜测仍可能被 Auto-save、明确修正旧
thesis 没有 replacement/supersede、模型把普通业务语义误判为 Memory MCP 管理命令调用 mutation 工具。
另外 relation semantic dedupe 与 revoke cascade 已实现但需补回归测试。

## What Changes

- **A1 explicit_uncertainty -> Pending**：`ConservativeAdmissionPolicy.decide` 在 system_inference 检查前
  增 `has_explicit_uncertainty(candidate)`：source_expression/content 命中明确不确定/猜测/未验证模式
  （只是猜测/也许/暂/不确定/没有足够证据/hypothesis…）-> PENDING(explicit_uncertainty)。优先级
  高于 explicit_durable_statement。不新增 epistemic_state 字段、不改 assertion_kind 枚举。
- **A2 replacement target fallback**：`_resolve_semantic_target` 对 `_is_explicit_replacement(candidate)`
  且字面 subject 未命中的情况，用更宽松的 `_REPLACEMENT_FALLBACK_THRESHOLD=0.45` 查同 owner+profile+type
  旧 active memory，找到即作为 replacement 目标（新旧判断措辞不同但仍语义相关）。仅在用户明确替换 +
  唯一明显目标时执行，否则走新增/ambiguous。prompt 补「明确修正时输出一个完整 thesis candidate」。
- **C1/C3 tool scope 隔离**：`link_memories` 从 memory:write 改为 memory:review；新增权威 `TOOL_SCOPES`
  映射（ListTools 过滤 + CallTool 硬授权共用）；`MemoryMcpServer.list_tools` 按 principal scopes 过滤可见
  工具（capture_completed_turn 对仅有 memory:write 的 Hook token 不可见）。
- **D2 relation semantic dedupe 回归**：InMemory + PostgreSQL 已有 partial unique index / 语义 lookup
  去重；补 manual+automatic 语义等价边去重回归测试。
- **E revoke cascade 回归**：已有 `stale_at=now()` 修复 + cascade stale；补测试。

## Capabilities

### New Capabilities

- `uncertainty-replacement-tool-isolation`：规定 explicit uncertainty -> Pending、replacement target
  fallback、tool scope 隔离（link_memories review + ListTools 过滤 + CallTool 硬授权）、relation semantic
  dedupe、revoke cascade 回归。

### Modified Capabilities

无。

## Impact

- Core：`admission.py` 增 `has_explicit_uncertainty` + decide 顺序调整；`candidate_processing.py` 增
  `_REPLACEMENT_FALLBACK_THRESHOLD` + `_resolve_semantic_target` fallback。
- Tools：`tools/shared.py` 增 `TOOL_SCOPES`/`visible_tool_names`/`authorize_tool_call`；`tools/memory.py`
  `link_memories` WRITE->REVIEW。
- App：`app.py` `MemoryMcpServer.list_tools` 按 scope 过滤。
- 提取层：`backends.py` prompt 补 replacement 引导。
- 测试：capture_service 增 explicit_uncertainty、manual+auto dedupe；既有 revoke cascade 测试。
- 不改 MCP DTO、Admission 保守原则、Relation/Recall 阈值、DB schema、Core 自包含不变量。
