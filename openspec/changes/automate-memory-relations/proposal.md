## Why

关系底座已经能够安全保存、撤销和召回记忆关系，但当前只能由调用方显式执行 `link_memories`，没有进入主动记忆的 AfterRun/Capture 闭环。用户只配置 MCP 地址和 Token 时，系统应当在服务端依据 Profile 规则自动识别高置信关系，同时把模型输出继续视为不可信建议。

## What Changes

- 在通用 Capture 流程中增加可选的结构化关系抽取阶段；没有关系策略的 Profile 直接跳过，不增加模型调用。
- 关系抽取只接收脱敏轮次、已通过准入的本轮记忆和 owner-scoped 有效记忆的有界摘要；模型只能引用服务端分配的临时引用或已提供的 memory ID。
- 由通用 Core 重新校验 Profile 允许的关系类型、方向、owner、Profile、端点状态、置信度和唯一性，禁止模型提供或覆盖 owner。
- 对“两个端点都已确定、命中用户原文、关系唯一、显式且高置信”的建议自动建边；Assistant/Tool 自述、歧义、低置信和依赖 pending/blocked 端点的建议在 v1 中安全跳过并记录无正文原因，不生成半有效关系。
- 将自动关系与本轮记忆结果放入同一个 Repository 捕获事务，维持幂等重放和失败原子性；不引入 Agent 端配置、队列或新的运行服务。
- 复用同一个聊天模型和 Provider 配置构造候选与关系抽取器，避免两套模型配置；关系抽取失败不会提交部分捕获结果，而是进入现有可重处理状态。
- 保留 `link_memories` 和 `revoke_memory_relation` 作为治理/修正能力；端点失效继续自动停止关系参与读取与召回。
- 增加日志、单元/Repository/MCP 回归测试，以及设计、配置、使用和测试文档。

## Capabilities

### New Capabilities

- `automatic-memory-relations`: 定义 Profile 驱动的服务端关系抽取、安全准入、捕获事务、幂等和失败语义。

### Modified Capabilities

无。当前关系底座仍位于未归档的 `add-memory-relations` 变更中，本变更在其上增加独立的自动化合同。

## Impact

- Core：关系抽取 port/领域建议、自动关系规划器、CaptureService 与 CaptureWrite。
- 模型适配：扩展现有 extraction 包，复用同一 ChatModel，增加严格关系 schema 和受限提示词。
- 存储：进程内与 PostgreSQL `commit_capture` 原子写入关系；复用 `0006` 表结构，不修改已部署 migration。
- 运行行为：仅启用关系策略的 Profile 在有可关联端点时增加一次结构化模型调用；`general-work` 行为和成本不变。
- MCP/Agent：工具参数、Hook 协议、地址和 Token 配置不变，不要求 Agent 安装服务端包。
- 非目标：关系待确认 UI、实体消歧、知识图谱、多跳推理、自动判断一条现有关系语义已失效、队列化 Capture。
