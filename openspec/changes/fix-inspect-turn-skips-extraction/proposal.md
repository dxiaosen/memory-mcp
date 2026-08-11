## Why

2026-08-11 真实联调日志（`.memory-mcp/logs/memory-mcp.log`）暴露：AfterRun 钩子对
每一轮 `Stop` 都调 `capture_completed_turn`，不区分业务对话 turn 与"查看/管理已存储
记忆"的 inspect/manage turn。14:16:57 的 inspect turn（user="请显式查看 Memory MCP
中当前属于我的青禾食品记忆。"，assistant 输出 5 条记忆表格）触发了完整抽取链路：

- 候选抽取 1.28s 产出 0（inspect 内容无业务事实）
- 关系抽取 1.69s 产出 0（3 次重试全 `invalid_source_expression`——user 原文"请显式查看
  记忆"不含任何 source_expression 片段，`trusted_user_sources` 校验必失败）
- 总 3.3s + 2 次模型调用全白烧

6 个 turn 里 2 个是 inspect（33%），每个白烧 ~3s。按 CLAUDE.md 测试边界铁律，inspect/manage
turn 不应参与抽取。

## What Changes

- **Agent Host Adapter 在 AfterRun 投递 capture 前检测 inspect/manage turn**：基于结构性
  信号——当前轮次 transcript 中 assistant 是否调用了 memory 管理类工具
  （`search_memories`/`list_memories`/`get_memory`/`revoke_memory`/`link_memories`/
  `list_pending_reviews`/`confirm_pending_memory`/`reject_pending_memory`/`batch_confirm_pending`/
  `revoke_memory_relation`/`get_memory_stats`）。inspect/manage turn 的 assistant 必然调用
  其中至少一个来查看或操作记忆；业务 turn 的 assistant 只依赖 BeforeRun hook 自动调的
  `recall_memory`，不调这些。
- **`recall_memory` 不计入信号**：BeforeRun hook 每个业务 turn 都自动调它，若计入会把
  所有业务 turn 误判为 inspect。`capture_completed_turn` 是 hook 自身投递通道，也不计入。
- **命中即跳过**：`reason_code=inspect_or_manage_turn`，走既有 `_skip_after` 路径，与
  `missing_final_output`/`missing_turn_state` 一致地记 `agent_hook.capture.skipped` WARNING
  + 返回 `AgentHookOutcome(warning_code="inspect_or_manage_turn")`，不调 capture。
- **安全降级**：无 `transcript_path`（通用合同 / 非 Claude Code 宿主）或解析失败时
  不跳过，保持现有 capture 行为（宁可白烧一次抽取也不误伤业务 turn）。
- **transcript.py 新增 `collect_turn_tool_uses`**：返回当前轮次 assistant 块所有 tool_use
  工具名集合，复用既有 `_iter_jsonl`/`_slice_current_turn` 辅助，best-effort 不抛。

## Impact

- **不改 Server / Core / contract 边界**：不加 turn 意图字段，`CompletedTurnEventV1`/
  `TurnEnvelope`/`capture_completed_turn` 工具签名不变。inspect/manage 判断词义留在
  Agent 侧（hosts.py + transcript.py），不进 Core，不违反铁律 3。
- **不改 schema / 指纹 / 召回打分常量**。
- **既有 skip 语义不变**：`missing_final_output`/`missing_turn_state` 行为不受影响。
- **文档**：`docs/design.md` §10.2 AfterRun 流程图 + 说明、`docs/logging.md`
  `agent_hook.capture.skipped` 行更新。
