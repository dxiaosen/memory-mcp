# Design: 模型自主调用 capture（Phase 1）

## 分阶段决策

转向分两阶段，**不捆在一起 PoC**：

### Phase 1（本变更）：gate + 契约简化
- 去掉 Stop hook 强制捕获，AfterRun 完全 no-op。
- capture 工具契约简化：模型传内容，服务器组装身份/幂等字段。
- 保留服务端二次抽取。
- 接受失败即丢（无 outbox/重试）。

**PoC 验证一件事**：模型 gate 得准不准（该调的调、不该调的不调）+ 漏捕获率。

### Phase 2（按 PoC 数据决定，不在本变更）：模型携带候选跳过二次抽取
- 若 Phase 1 证明模型 gate 准但二次抽取在已 gate 的轮上仍过度抽取，再加"模型产候选、
  服务器跳过二次抽取"。
- **不捆的原因**：gate 赌的是模型判断能力，curation 赌的是模型结构化产出能力。
  捆一起 PoC 失败分不清哪个失败。且"带候选"的动机（二次抽取过度）在 gate 后大幅削弱——
  过度抽取主要场景（inspect/搜索轮）在 gate 后模型根本不调 capture，剩余业务轮的二次
  抽取未必是问题，Phase 1 很可能就够。

## 关键设计点

### event_id 服务器派生（不 client 生成）

旧设计：bridge `_event_id(context)` 用 `uuid5(NAMESPACE_URL, run_key)` 生成 event_id，
client 传给服务器。问题：跨进程 NAMESPACE 差异、client 漂移破坏幂等。

新设计：服务器 `to_turn_envelope` 用 `sha256(owner_id|conversation_id|turn_id)` 派生
`memory-agent:{digest}`。owner_id 由认证上下文派生（铁律 2），conversation_id/turn_id
由模型传。幂等性从"client 生成"转移到"服务器从三元组派生"，更可靠。

### payload_fingerprint 基于简化输入

旧设计：`CompletedTurnEventV1.payload_fingerprint()` 基于完整 `model_dump()`（含
event_id/messages/observed_at）。新设计：`CompletedTurnInputV1.input_fingerprint()` 基于
`{conversation_id, turn_id, user_input, final_output, subject_hint}`——不含 event_id
（服务器派生）和 profile_id（跨 profile 重投无意义）。检测同一 event_id 是否被不同内容
重用 → IdempotencyConflictError。

### observed_at 服务器时钟

旧设计：client 传 `observed_at`（hook 进程 `datetime.now(UTC)`）。新设计：服务器
`clock()`（`MemoryService.clock` 属性，单一时间权威）。消除 client 时钟漂移。

### bridge.after_run_success 保留但简化

`bridge.after_run_success` + `HookedAgentRunner`（runner.py）保留作为编程式接入参考
实现。签名简化：删 `observed_at`/`document_messages` 参数。`_capture` 不再生成 event_id/
observed_at，改调 `client.capture_completed_turn(conversation_id=, turn_id=, user_input=,
final_output=, ...)`。`AfterRunResult.event_id` 改名 `event_ref`（仅日志关联用，
`memory-bridge:{uuid5}`）。

### document provenance 退化（已知债务）

capture 不再带 document 消息（`extract_document_messages` 保留但不再被 hosts 调用）。
`EvidenceSourceType.DOCUMENT` 来源本阶段不产出。投资研究读纪要文件的 provenance 影响
召回排序——已知债务。`extract_document_messages` 函数保留，Phase 2 可恢复。

### 失败即丢取舍

hook 时代有 3 次重试 + outbox 跨轮次补投，现在变 0。对"不丢"为核心价值的记忆系统，
这是有分量的取舍。PoC 可接受，但若实测发现网络抖动导致漏捕获，需补轻量重试
（不一定 outbox，MCP 客户端层或模型自身重试可能够）。
