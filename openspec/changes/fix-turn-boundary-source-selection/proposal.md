## Why

2026-08-08 真实联调日志（`.memory-mcp/logs/memory-mcp.log`）暴露 4 个 Turn 边界与
Source 选择问题，导致第二轮捕获重复历史文档、用户明确长期判断被绑定到 assistant 复述、
真实跨行原文被误杀、team 提取按成员重复计数：

1. **Turn 边界缺失**：第一轮 `capture_completed_turn` 的 `message_count=11`，把整个会话
   transcript（9 条 tool/document + user + assistant）全部发送，而非只发当前 turn。第二轮
   会重复包含第一轮的 tool message。
2. **source_expression 精确匹配误杀**：真实存在于原文的表达因换行/空格差异被判
   `invalid_source_expression`。
3. **用户原文未优先**：同一语义同时出现在 user 与 assistant 时，候选被绑定到 assistant
   复述（`source_role=assistant`、`system_inference`），用户的 thesis/risk/
   ongoing_research/research_preference 被挡在 `non_user_source` 之外无法 auto_save。
4. **Team 提取重复计数**：`team_count=3` 但连续三次同一 `team_owner_ref`--按成员展开
   产生同一 team owner，未在 batch 前按 `team_owner_id` 去重。

## What Changes

- **Turn 边界由 Agent Host Adapter 负责**：`extract_document_messages` 增 `user_prompt`
  参数，定位最近一次用户文本输入作为当前轮次边界，只提取其后的 tool/document 消息。
  `hosts.py` 透传 `saved.prompt`。Server 不感知 Claude Code transcript 结构。
- **source_expression 空白归一化匹配**：校验与来源定位改为
  `normalize(source_expression) in normalize(source)`，normalize 仅 `\s+ -> 单空格` + trim，
  不做 NFKC/casefold（不改写字符）。真实原文 + 仅空白差异 -> valid；模型拼接独立 bullet
  -> 仍 invalid（严格性保留）。
- **用户原文优先绑定**：`_source_metadata` 选中逻辑改为显式优先级
  `user > tool > assistant`；extraction prompt 引导用户自身判断的 source_expression 取
  用户原文逐字片段而非 assistant 复述。
- **Team 提取去重**：`TeamExtractionService` 构造时按 `team_owner_id` 去重 team_configs，
  合并同 team 的 `member_owner_ids`（并集保序）。

## Capabilities

### New Capabilities

- `turn-source-selection`：规定 CompletedTurnEvent 的当前轮次边界、source_expression 空白
  归一化校验、用户原文优先的来源绑定，以及 team 提取按 team_owner_id 去重。

### Modified Capabilities

无。本变更以独立增量能力描述新增行为。

## Impact

- Agent：`transcript.py` 增 `user_prompt` 参数与 `_slice_current_turn`/`_user_text` 辅助；
  `hosts.py` 透传 `saved.prompt`。
- Core：`candidate_processing.py` 增 `_normalize_source_text` 并用于校验与 `_source_metadata`；
  `_source_metadata` 选中逻辑改为显式优先级（新增 `_select_source_message`）。
- 提取层：`backends.py` `_system_prompt` 增用户原文优先与原子化（单 span）引导。
- Core：`team_extraction_service.py` 构造时去重 team_configs（新增 `_dedup_team_configs`）。
- 测试：agent_transcript 增多轮切片用例；agent_hosts 增两轮 E2E；capture_service 增归一化/
  严格性/用户优先用例；team_extraction 增去重用例。
- 不改对外 DTO（Candidate/Evidence/CaptureOutcome/CompletedTurnEventV1 字段不变）、不改
  Admission/Lifecycle 决策规则、不改 DB schema、不改日志事件/字段、不改 Core 自包含不变量。
