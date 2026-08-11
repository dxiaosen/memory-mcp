## Why

2026-08-11 真实联调暴露 AfterRun Stop hook 强制每轮捕获的系统性问题：每一轮对话无论是否
含持久信号都被捕获抽取，导致 inspect/manage turn、搜索 turn、闲聊 turn 全部白烧模型调用。

- 15:10 搜索 turn 抽 10 候选 0 auto_save 9 pending（搜索背景不该记）
- 15:12 inspect turn 把 6 条已存记忆当新候选（assistant 复述被当新事实）
- inspect skip 逻辑（`fix-inspect-turn-skips-extraction`）虽实现但跨环境不可靠——依赖
  `transcript_path`，而 hook 进程在本地笔记本、Server 在远程，transcript 解析在 hook 侧
  失效时根本不触发 skip。

根因不是"inspect 判断不准"，而是**架构假设错了**：hook 不应该决定每轮都捕获，应该让模型
自主判断这轮是否值得记。模型有完整对话上下文，比 hook 端结构性信号判断准得多，且不依赖
transcript 跨环境可用性。

## What Changes

分两阶段实施，本变更只做 **Phase 1**（gate + 契约简化），Phase 2（模型携带候选跳过二次
抽取）按 PoC 数据另行决定。

### Phase 1 范围

- **去掉 AfterRun Stop hook 捕获路径**：`hosts.py` `_after` 改为完全 no-op，不再
  `stage_capture`/`_deliver_staged`/`_retry_one_pending`。`_before` 不再 `save` TurnState
  （outbox 不再写入）。本地存量 outbox 靠 24h TTL 清空。
- **移除 inspect/manage 跳过逻辑**：`_MEMORY_MANAGEMENT_TOOLS`/`_is_inspect_or_manage_turn`/
  `collect_turn_tool_uses` 全删——模型自主调用后，inspect/manage 轮模型根本不会调
  capture，跳过逻辑冗余。
- **简化 capture 工具契约**：模型只传 `conversation_id`/`turn_id`/`user_input`/
  `final_output`（+ 可选 `profile_id`/`subject_hint`）。删 `event_id`/`contract_version`/
  `observed_at`/`messages`。服务器在 `CompletedTurnInputV1.to_turn_envelope` 组装身份与
  幂等字段：`event_id = memory-agent:{sha256(owner_id|conversation_id|turn_id)}`，
  `observed_at = clock()`，`contract_version = "1"`，`payload_fingerprint` 基于简化输入计算，
  `messages` 由 `[user, assistant]` 两条组装。
- **保留服务端二次抽取**：`StructuredCandidateExtractor` 不动。gate 关掉后过度抽取主要
  场景（inspect/搜索）消失，剩余业务轮的二次抽取未必是问题。
- **保留 bridge.after_run_success + HookedAgentRunner**：作为编程式接入参考实现，
  `client.capture_completed_turn` 签名同步简化（不再传 event_id/observed_at）。
- **接受失败即丢**：无 outbox/重试，网络抖动 → 漏捕获，靠模型后续轮次自然重试。

### Phase 2（不在本变更范围）

若 Phase 1 PoC 证明模型 gate 准但二次抽取在已 gate 的轮上仍过度抽取，再加"模型携带候选
跳过二次抽取"。两个信任赌注不捆在一起 PoC。

## Impact

- **Server / Core 边界不变**：`capture_turn(principal, TurnEnvelope)` 接口不变，
  `TurnEnvelope` 字段不变；`_capture_turn_locked` 幂等/conflict 逻辑不变（event_id/
  payload_fingerprint 改由服务器组装，来源更可靠）。不违反铁律 1/2/4。
- **Core 自包含**：schemas.py 属于 root 包非 core，新增 `CompletedTurnInputV1` 不引入
  core 依赖。`MemoryService.clock` 属性只暴露已有时钟 callable。
- **schema 不变**：`payload_fingerprint` 仍存 `captures` 表，只改计算来源。
- **document provenance 退化**：capture 不再带 document 消息，`EvidenceSourceType.DOCUMENT`
  来源本阶段不产出——已知债务，Phase 2 或独立机制恢复。
