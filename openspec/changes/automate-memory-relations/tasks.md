## 1. Relation extraction contract

- [x] 1.1 Add untrusted relation proposal, endpoint request and extractor port types with strict invariants and bounded constants
- [x] 1.2 Add structured relation backend/schema/prompt and adapter parsing with exact source and identity-field rejection
- [x] 1.3 Build candidate and relation extractors from one configured ChatModel without adding environment settings

## 2. Capture planning and transaction

- [x] 2.1 Implement generic endpoint selection and conservative automatic relation planning from Profile policies
- [x] 2.2 Integrate the optional relation stage into CaptureService/MemoryService composition with skip, failure, replay and safe logging semantics
- [x] 2.3 Extend CaptureWrite validation plus in-memory and PostgreSQL transactions to atomically persist idempotent automatic relations

## 3. Verification

- [x] 3.1 Add domain/adapter tests for strict output, endpoint limits, Profile direction, confidence, explicit evidence and duplicate proposals
- [x] 3.2 Add Capture tests for general-work skip, same-capture/existing endpoints, pending/blocked exclusion, owner isolation, failure retry and replay
- [x] 3.3 Add Repository/PostgreSQL contract tests for atomic relation writes, active duplicate idempotency and rollback boundaries

## 4. Documentation and acceptance

- [x] 4.1 Update design, configuration, Agent/Hook usage, logging and testing documents with automatic relation behavior and limitations
- [x] 4.2 Run format, static checks, full tests, package builds, document conflict search and strict OpenSpec validation
- [x] 4.3 Review dependency direction, configuration surface, transaction behavior and migration compatibility; record final evidence

## Acceptance evidence

- `pytest`: 140 passed, 8 skipped；跳过项均为需要显式测试库 URL 的破坏性 PostgreSQL 测试。
- `ruff format --check`、`ruff check`、`git diff --check`：通过。
- `uv build --all-packages`：Server 与轻量 Agent 的 sdist/wheel 均构建成功；Server wheel 包含 `0006` 和自动关系实现，Agent wheel 不包含 Server 模块。
- 全部活动 OpenSpec 变更严格校验通过，当前文档未发现“只能手工建边”等冲突说明。
- 自动关系复用现有 ChatModel 与配置，不新增环境变量或 Agent 依赖；`general-work` 未声明关系策略，因此不触发第二次模型调用。
- Core 仍只依赖端口和领域类型；LangChain、MCP、PostgreSQL 实现分别留在 adapter/transport 边界。
- 新记忆、替代 revision 与关系由同一个 `CaptureWrite` 和 Repository 事务提交；失败时共同回滚，事件重放不再次调用模型。
- 未修改已部署的 `0006_memory_relations.sql`，也未新增 migration；自动化代码回滚后已有关系仍由关系底座读取和治理。
