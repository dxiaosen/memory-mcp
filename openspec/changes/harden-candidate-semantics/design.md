# 设计决策

## D1：assertion_kind 归一化放在候选可信化阶段，不信任模型自报

**决策**：在 `candidate_processing.py` 的 `_normalize_assertion_kind` 函数中，按
`source_metadata["source_role"]` 和 `source_metadata["source_type"]`（二者均由
`_source_metadata` 从可信 `turn.messages` 派生，不信任模型自报字段）纠正
`proposal.assertion_kind`，在构造 Candidate 前覆盖。规则：

- `source_type in (tool, document, web)` + `reported in (user_view, user_provided_fact, system_inference)`
  → `external_fact`（外部材料提供的客观信息）。
- `source_role=assistant` + `reported in (user_view, user_provided_fact)` → `system_inference`
  （Assistant 自己的分析/推断）。
- 其余（用户来源、或已正确标注）返回 `None`，保持原值。

**理由**：模型常把 Assistant 的 thesis 标成 `user_view`（日志已证实），过度依赖模型自报
会产生语义污染。`source_role` / `source_type` 由可信消息块派生，是纠正的正确依据。

**不做一刀切**：不把 `source_role != user` 简单映射成 `system_inference`，因为 Assistant
可能只是转述外部材料事实（应为 `external_fact`）。区分转述与推断需读语义，无法仅靠角色判别，
故 tool/document/web source_type 通道单独兜底为 `external_fact`，其余 assistant 标注
先统一降为 `system_inference`。

## D2：confirm_review 不重写 assertion_kind，与 verification_status 解耦

**决策**：`review_service.py` 的 `confirm` 路径只设 `verification_status=USER_CONFIRMED`，
不重写 `assertion_kind`。`assistant-generated system_inference` 被 Confirm 后仍是
`system_inference`，不会历史性地变成 `user_view`。

**理由**：`assertion_kind`（内容的知识性质）与 `verification_status`（内容是否被验证）
是独立维度，recommend.md §4.5 明确要求二者不耦合。已验证 `materializer.record/duplicate/
replacement` 均直接保留 `candidate.assertion_kind`，无需改动。

## D3：候选数量三层控制，保留硬上限 20 + 软裁剪 12

**决策**：三层控制，不降低 `MAX_CANDIDATES` 硬上限：

1. **prompt**：`_system_prompt` 增"Aim for 5 to 10 candidates; never exceed 12"。
2. **capture_guidance**：investment-research profile 增数量指导（更改 profile 指纹，
   已同步注册）。
3. **后置软裁剪**：`StructuredCandidateExtractor.extract` 解析后若 `len(proposals) > 12`，
   按 `confidence` 降序取前 12，记 DEBUG `memory.capture.candidates_truncated`。

**理由**：最初考虑直接降 `MAX_CANDIDATES` 到 12，但这会让模型返回 13 条时整轮失败
（schema `max_length` 越界 → `InvalidModelOutputError`），违背 P0-B"单条坏候选不拖垮整轮"
的精神。保留 20 硬上限作为"模型偶尔多出几条"的缓冲，12 软裁剪作为"正常上限"。
软裁剪按 confidence 降序，保留模型最有把握的候选。

## D4：文件来源 provenance 在 Host Adapter 解析，Core/Server 不感知 Claude Code 格式

**决策**：在 Agent 包新增 `transcript.py` 模块，纯函数解析 Claude Code transcript JSONL，
还原 `Read` 工具调用 + `tool_result`，产出通用 `RoleMessageV1` 风格的 `role=tool /
source_type=document` 消息字典。Host Adapter 在 AfterRun 解析、将文档消息持久化进
TurnState outbox、随 `capture_completed_turn` 的 messages 投递。

**理由**：recommend.md §5.4 明确要求 Core 不直接解析 Claude Code transcript、Server 不依赖
Claude Code 格式。正确位置是 Host Adapter → 通用 CompletedTurnEventV1 → Server。
Server 端 `RoleMessageV1` schema 与 `TurnMessage` 已全面支持 tool/document 来源字段，
无需改动 Server 端契约。

**文档消息持久化**：解析出的文档消息随 TurnState outbox 持久化，使重投不依赖 transcript
文件是否仍存在（Claude 会轮换/清理 transcript）。fingerprint 包含 `document_messages`
摘要以保证幂等。

**best-effort**：transcript 不可读、结构非法、或无文件读取工具调用时返回空列表，不阻断
capture 主流程——provenance 增强是增量能力，不能让 transcript 问题拖垮记忆捕获。

## D5：零召回返回空渲染，不注入占位文本

**决策**：`recall_service.py` 的 `_empty_result` 与内联 fallback 均返回
`rendered_context=""` / `estimated_tokens=0`，移除 `"No relevant historical user context
was recalled."` 占位文本。`_NO_RELEVANT_CONTEXT` 常量保留但不再注入。

**理由**：recommend.md §6 明确指出占位文本无业务价值、无意义消耗 token、给业务模型
一条无价值上下文。空字符串 + 0 token 是"没有记忆时不向 Agent 注入占位文本"的正确表达。
