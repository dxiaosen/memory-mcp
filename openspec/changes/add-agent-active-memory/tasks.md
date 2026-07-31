## 1. 配置与公共合同

- [x] 1.1 将 Agent Host 首选连接变量收敛为 `MEMORY_MCP_URL` 和 `MEMORY_MCP_TOKEN`，保留旧变量兼容并测试优先级
- [x] 1.2 为 `recall_memory` 和 `capture_completed_turn` 增加内部 `general-work` 默认值，并更新合同测试

## 2. 主动记忆适配器

- [x] 2.1 实现 Codex/Claude Code 公共 Hook 输入解析、轮次标识规范化和严格 JSON 输出
- [x] 2.2 实现权限受限、原子写入、精确匹配和过期清理的本地轮次状态
- [x] 2.3 实现 `UserPromptSubmit` 主动召回与 `Stop` 自动捕获，复用 `MemoryHookBridge` 的 fail-open、重试和幂等语义
- [x] 2.4 增加 `memory-mcp-hook` CLI 入口及不泄露内容/Secret 的核心阶段日志
- [x] 2.5 将宿主输入、通用 `AgentTurnEvent`/`AgentHookOutcome` 和 command Hook JSON 渲染解耦，支持标准 `BeforeRun`/`AfterRun`

## 3. 宿主兼容与测试

- [x] 3.1 增加 Codex 与 Claude Code 事件契约、并发轮次、缺失状态、失败和清理单元测试
- [x] 3.2 增加真实 MCP transport 的主动召回与捕获集成测试，并确认 owner 隔离
- [x] 3.3 运行完整 pytest、ruff 和 OpenSpec validate，修复所有回归
- [x] 3.4 增加第三方通用 Agent 合同测试，确认扩展不需要修改 Bridge、状态或 Core

## 4. 配置与文档收尾

- [x] 4.1 更新 Agent 配置模板和配置参考，使快速开始只要求地址与 Token
- [x] 4.2 编写通用 Agent 合同及 Codex/Claude Code 内置配置、版本要求、信任步骤、端到端手工测试和故障排查
- [x] 4.3 更新整体设计、测试、部署、README 和文档索引，并复查命名、目录结构、回滚与限制说明
