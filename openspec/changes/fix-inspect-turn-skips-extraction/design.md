# Design: inspect/manage turn 跳过抽取

## 背景

AfterRun 钩子对每一轮 `Stop` 都调 `capture_completed_turn`，不区分业务对话 turn 与
inspect/manage turn（查看/管理已存储记忆）。14:16:57 的 inspect turn 触发完整抽取
链路：候选抽取 1.28s 产出 0 + 关系抽取 1.69s 产出 0（3 次重试全
`invalid_source_expression`），总 3.3s + 2 次模型调用全白烧。

## 设计决策

### 决策：基于 transcript 检测 memory 管理工具调用，Agent 侧跳过 capture

**信号选择对比：**

| 信号 | 可靠性 | 缺点 |
| --- | --- | --- |
| user_input 关键词（"查看/撤销/确认...记忆"） | 脆弱 | "我看下毛利率""帮我确认这个判断"误伤；中文自由文本难穷举 |
| **transcript 检测 memory 管理工具调用** | **结构性、最可靠** | 依赖 transcript_path；无 transcript 时降级为不跳过（安全） |
| Server 侧从 messages 推断 | 违反铁律 3 | Core 不含 inspect/manage 词义；Server 无 turn 意图入参 |

选**transcript 信号**。依据日志实锤：inspect turn 的 assistant 必然调用至少一个 memory
管理工具（`search_memories`/`list_memories`/`revoke_memory`/...），而业务 turn 的 assistant
只依赖 BeforeRun hook 自动调的 `recall_memory`，不调这些管理工具。

**为何 `recall_memory` 不计入信号：** BeforeRun hook 每个业务 turn 都自动调它，若计入
会把所有业务 turn 误判为 inspect。`capture_completed_turn` 是 hook 自身投递通道，也不计入。

**为何在 Agent 侧而非 Server 侧：**
1. 铁律 3 要求 Core 不含场景词义；inspect/manage 判断属场景语义，留在 Agent 侧
   （hosts.py + transcript.py），不进 Core。
2. Agent 侧是宿主适配层，本就感知 transcript 与宿主工具调用；Server 侧无 turn 意图
   入参，加字段要穿透 `CompletedTurnEventV1`/`TurnEnvelope` 契约边界，改动面大且污染契约。
3. 跳过发生在 capture 调用之前，Server/Core 完全不感知——最小侵入。

**安全降级：** 无 `transcript_path`（通用合同 / 非 Claude Code 宿主）或解析失败时
不跳过，保持现有 capture 行为。理由：宁可白烧一次抽取（产出 0，无害），也不误伤
业务 turn（漏捕获业务事实不可逆）。这与 transcript.py 既有 best-effort 策略一致
（provenance 增强失败不阻断主流程）。

### 边界与风险

- **漏检**：用户说"看看我的记忆"但 assistant 只凭 BeforeRun 注入复述、没调管理工具 →
  不跳过，抽取产出 0（无新事实），浪费一次抽取但无害。可接受。
- **误检**：业务 turn 的 assistant 偶然调了 memory 管理工具（如业务讨论中顺手
  search_memories 查证）→ 会被跳过，该 turn 的业务事实不捕获。但这种情况罕见
  （业务 turn 一般不调管理工具），且用户可在下一轮补说。权衡后可接受；
  若实测发现问题再调信号策略。
- **transcript 不可用**：通用合同 / Codex 无 transcript_path → 不跳过，保持现状。

## 影响面

| 文件 | 改动 |
| --- | --- |
| `agent/.../transcript.py` | 新增 `collect_turn_tool_uses` 函数 |
| `agent/.../hosts.py` | `_after` 加 inspect/manage 跳过判断 + `_MEMORY_MANAGEMENT_TOOLS` 常量 + `_is_inspect_or_manage_turn` 辅助 |
| `tests/integration/test_agent_hosts.py` | +2 测试（inspect 跳过 / 业务不误跳） |
| `docs/design.md` §10.2 | AfterRun 流程图 + 说明更新 |
| `docs/logging.md` | `agent_hook.capture.skipped` 行补 `inspect_or_manage_turn` reason_code |
