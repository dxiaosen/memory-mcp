# 阶段一验收记录

对应 OpenSpec 变更：`add-general-memory-core`。

代码结构和设计说明见
[Memory Core 阶段一详细设计与代码导读](phase-one-design.md)。

## 验收范围

- 通用 Core 与场景插件的依赖边界；
- `ScenarioPolicy` 和显式场景注册；
- `MemoryItem`、初始 `MemoryRevision` 和 `Evidence`；
- 可信 `PrincipalContext`；
- owner-scoped 手动创建、当前列表、历史列表和详情读取；
- SQLite 健康检查、版本化迁移和真实持久化 Repository；
- 已注册场景/类型、合法状态、来源归属和单一当前 revision 约束；
- 中性测试场景、跨用户负向测试和依赖守卫。

## 验收命令

```powershell
uv run pytest tests/memory
uv run ruff check src/agent_lab/memory tests/memory examples/memory_phase_one.py
uv run ruff format --check src/agent_lab/memory tests/memory examples/memory_phase_one.py
uv run python examples/memory_phase_one.py
uv run python -m agent_lab.memory.adapters.sqlite.runtime migrate
uv run python -m agent_lab.memory.adapters.sqlite.runtime health
openspec-cn validate add-general-memory-core --strict
```

## 环境说明

阶段一权威存储为 Python 标准库自带的 SQLite，不需要安装数据库服务、
Docker 或数据库驱动。默认数据库文件为 `.agent-lab/memory.db`，可通过
`MEMORY_DATABASE_PATH` 改为其他路径。

测试为每个用例创建独立 SQLite 文件并在结束后清理，数据库迁移、真实读写、
进程间重新打开、完整性约束和 owner 隔离均可在公司电脑离线验证。
`InMemoryMemoryRepository` 仍保留为快速单元测试替身，但不承担持久化验收。

## 本机结果

- Memory Core（含真实 SQLite）：24 项全部通过，无跳过；
- 全量回归：42 项全部通过；
- 统一日志配置、文件滚动、幂等初始化和敏感字段脱敏测试：通过；
- SQLite 迁移幂等、`PRAGMA quick_check`、重新打开持久化：通过；
- 已注册类型、合法状态、必需来源、单一当前 revision、跨 owner 来源约束：通过；
- 场景注册失败不污染进程注册表、SQLite 类型目录同步：通过；
- 顶层包轻量导入、Knowledge/Memory 配置独立加载：通过；
- Ruff 静态检查和格式检查：通过；
- 阶段一 SQLite 演示：通过；
- OpenSpec 严格校验：通过。

## 基线说明

原有 `tests/test_loaders.py` 缺少 `tests/fixtures/knowledge` 夹具的问题已补齐；
知识文件加载和不支持文件类型两项测试现均通过，未修改知识库加载逻辑。
