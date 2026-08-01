## Why

项目已经完成核心、Agent、投研关系和质量评测闭环，但连续迭代留下了测试专用代码进入生产包、旧配置别名、重复测试以及多份重复说明。继续扩展前需要做一次有边界的减法，降低维护成本，同时保留身份隔离、事务、安全和真实链路等高风险回归。

## What Changes

- **BREAKING**：Agent Client 连接配置只接受正式的 `MEMORY_MCP_URL` 和 `MEMORY_MCP_TOKEN`，删除旧 `MEMORY_HOOK_MCP_URL` / `MEMORY_HOOK_BEARER_TOKEN` 迁移别名。
- 从 Server 生产包删除仅供测试使用的 `FixedCandidateBackend`；确定性测试统一使用 `tests/support` 下的 Fake Extractor 注入。
- 删除或合并重复、只验证实现细节、以及“测试评测器自身”的低价值用例；保留 Core 安全、owner 隔离、幂等、事务、PostgreSQL、MCP Transport、Hook 生命周期和模型合同测试。
- 保留 `docs/design.md` 作为详细设计，压缩测试、使用、配置和导航中的重复内容；评测结果仍只在 `docs/evaluation.md` 维护。
- 删除与上级说明重复的评测结果目录 README，并修复所有受影响的文档引用和旧配置描述。

## Capabilities

### New Capabilities

- `repository-maintainability`: 生产包与测试替身隔离、回归集取舍和读者文档单一职责的维护合同。

### Modified Capabilities

无。

## Impact

- `agent/src/memory_mcp_agent/settings.py`：移除旧连接变量别名。
- `server/src/memory_mcp/extraction/`：移除测试专用 fixed backend 及公开导出。
- `tests/`：收敛重复测试并让确定性替身只存在于测试支持代码。
- `docs/`、`evals/`：压缩重复操作、测试历史和目录级说明。
- 不改变 MCP 工具、Core 领域语义、PostgreSQL schema、真实模型配置或发行包依赖；无需数据库 migration。
