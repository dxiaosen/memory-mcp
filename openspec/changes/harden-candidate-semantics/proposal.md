## Why

2026-08-07 真实联调日志暴露四个 P1 级候选语义与来源问题，影响投研记忆的质量、
可追溯性与召回性能：

1. **assertion_kind 语义冲突**：Assistant 阅读材料后形成的分析（如"增长质量在恶化"）
   被模型标成 `assertion_kind=user_view`，而它应是 `system_inference`。虽然因
   `source_role=assistant` 被降级为 pending 未污染 active memory，但语义不准确，
   且过度依赖模型自报，无人纠正。
2. **候选数量失控**：模型偶尔在一轮返回过多候选，淹没准入/去重管线、消耗召回预算，
   且无任何软裁剪与可观测。
3. **文件/工具来源 Evidence provenance 不完整**：材料数据（如"04 管理层交流纪要.md
   披露良率 78%"）最终记录为 `source_role=assistant / source_type=conversation /
   source_uri=null`，丢失文件名、路径、原始材料来源，投研场景无法回溯证据出处。
4. **零召回占位文本**：零结果时注入 `"No relevant historical user context was recalled."`
   占位文本，无意义消耗 token 并给业务模型一条无价值上下文。

这些问题在功能已跑通后直接影响投研记忆的语义准确性与证据可追溯性，应在扩展业务能力前收口。

## What Changes

- **assertion_kind 一致性归一化**：在候选可信化阶段（构造 Candidate 前）按可信来源
  角色/类型纠正模型自报的 `assertion_kind`——`assistant + user_view/user_provided_fact → system_inference`；
  `tool/document/web source_type + 任意非 external → external_fact`。用户来源不纠正。
  记一条 DEBUG `memory.capture.candidate.assertion_normalized`（from→to）。`confirm_review`
  保留原 `assertion_kind`，只改 `verification_status`，二者不再耦合。
- **候选数量控制（三层）**：
  - 提取 prompt 增"目标 5–10 候选、永不超过 12"；
  - investment-research profile `capture_guidance` 增数量指导（同步 profile 指纹）；
  - `StructuredCandidateExtractor.extract` 解析后若超过软上限 12，按 confidence 降序裁剪到 12，
    记 DEBUG `memory.capture.candidates_truncated`。硬上限 `MAX_CANDIDATES=20` 仍由 schema 强制。
- **文件来源 provenance**：Agent Host Adapter 解析 Claude Code `transcript_path`（Stop/
  UserPromptSubmit 事件提供），从 JSONL 还原 `Read` 工具调用 + `tool_result`，构造
  `role=tool / source_type=document / source_uri / source_title` 的消息，随
  `capture_completed_turn` 的 messages 投递。文档消息持久化进 TurnState outbox，
  使重投不依赖 transcript 文件是否仍在。解析 best-effort，失败返回空不阻断 capture。
- **零召回渲染**：零结果时 `rendered_context=""` / `estimated_tokens=0`，不再注入占位文本。
- 同步 logging.md 事件表与单测。

## Capabilities

### New Capabilities

- `candidate-semantics`：规定 assertion_kind 来源一致性归一化、候选数量软裁剪、
  文件来源 provenance 还原、以及零召回的空渲染契约。

### Modified Capabilities

无。本变更以独立增量能力描述新增行为；后续视归档时序同步到主规范。

## Impact

- Core：`candidate_processing.py` 增 `_normalize_assertion_kind` 并在构造 Candidate 前应用；
  `structured_model.py` 增软裁剪与 `candidates_truncated` DEBUG 事件；`recall_service.py`
  零召回返回空渲染。
- 提取层：`extraction/backends.py` `_system_prompt` 增数量与 assertion_kind 指导。
- Profile：`investment_research.py` `capture_guidance` 增数量指导，指纹更新；`profiles/__init__.py`
  注册新指纹。
- Agent：新增 `transcript.py` 模块解析 Claude Code transcript；`hosts.py` 透传
  `transcript_path` 并在 AfterRun 解析文档消息；`state.py` TurnState 增
  `document_messages` 字段并随 outbox 持久化；`bridge.py` / `client.py` 透传文档消息。
- 文档：logging.md 增 `assertion_normalized` / `candidates_truncated` /
  `transcript.parse_failed` / `transcript.document_messages_extracted` 事件行。
- 测试：capture_service 增 assertion 归一化三个用例；capture_adapters 增软裁剪用例；
  agent_hosts 增 transcript provenance 端到端用例；agent_transcript 增解析器单元用例。
- 不改 DB schema、不改 Admission 阈值、不改 Core 自包含不变量（`transcript.py` 在 Agent 包，
  Core 不感知 Claude Code 格式）。
