## Context

Memory MCP 已经具备通用记忆、投研 Profile、自动关系、主动 Agent hook 和离线/在线评测，但三处工程边界没有随能力演进同步收紧：

1. `general-work-v1`、`investment-research-v1` 只是一段人工维护的字符串。Profile 的元数据、关系和 Recall 策略变化后，捕获审计无法判断同名版本实际采用了哪套策略。
2. 捕获唯一键包含 `profile_version`。同一个 Agent 事件在发布新版本后重试，会绕过已有捕获记录。与此同时，Agent 默认显式传入 `general-work`，服务端认证配置没有机会为 Token 选择投研策略。
3. 评测直接调用 Recall 的私有打分函数，未经过生产服务的生命周期过滤、阈值、关系扩展、预算裁剪和最终渲染。PostgreSQL 端也会读取 owner/Profile 下全部有效记忆再在 Python 排序。

本变更横跨 Agent、MCP 传输、认证、应用服务、端口、PostgreSQL、评测和文档。领域/应用层仍不得依赖 MCP、HTTP、数据库驱动、Agent SDK 或运行时 Settings；owner 仍只能来自服务端认证上下文。

## Goals / Non-Goals

**Goals:**

- 每次捕获都能回答“使用了哪个 Profile 版本、哪份完整策略、哪个 Prompt 版本”。
- Profile 升级不改变同一来源事件的逻辑幂等身份。
- Codex、Claude Code 等轻量 Agent 只配置 MCP 地址和 Token 时，服务端即可选择默认 Profile。
- Recall 评测走与生产相同的公开应用服务入口。
- PostgreSQL Recall 候选读取有明确、可配置、可测试的硬上限。
- 迁移、配置和兼容行为在文档中可操作、无冲突。

**Non-Goals:**

- 不实现 OAuth/OIDC、动态租户管理或 Token 签发服务。
- 不引入 Celery、Redis、Kafka 等外部队列；现有进程内异步 hook 语义不变。
- 不引入向量数据库、embedding、PostgreSQL 扩展或中文全文索引。
- 不把 Profile 自动分类交给模型。默认 Profile 是受信身份配置，显式 Profile 仍是高级调用能力。
- 不借本变更重排包目录、重写关系算法或扩大评测数据集。

## Decisions

### 1. 以规范化策略指纹约束 Profile 版本

为 `MemoryProfile` 定义纯函数 `profile_fingerprint(profile)`：把影响捕获、关系和召回行为的声明式字段规范化为稳定 JSON，再计算 SHA-256。输入包含 Profile ID、允许的记忆类型与业务阶段、capture guidance、元数据策略、关系策略、Recall 优先级和提示；不包含 `profile_version` 本身，也不包含 Python 类名或字典插入顺序。

内置 Profile 升级为 v2，并维护 `(profile_id, profile_version) -> expected_fingerprint` 清单。构建内置 Profile registry 时必须校验；策略内容发生变化但开发者忘记升级版本和预期指纹时，启动测试和运行时装配均会失败。自定义 Profile 仍可计算和记录指纹，但不强制进入内置清单。

捕获记录新增 `profile_fingerprint`，与已有 `profile_version`、`prompt_version`、`schema_version`、`model_name` 一起形成审计快照。Prompt 仍单独版本化：修改抽取 Prompt 时必须更新 Prompt 版本；Profile 指纹不尝试覆盖模型参数或 Prompt 文本。

**Rejected alternatives:** 只升级版本字符串无法检测下次静默漂移；把完整 Profile JSON 存入每条捕获会增加重复数据和迁移复杂度；对 Python 对象直接 `repr` 哈希会受实现和字段顺序影响。

### 2. 幂等身份与处理策略版本分离

显式 `event_id` 的逻辑身份定义为 `(owner_id, event_id)`。无 `event_id` 的兼容身份定义为 `(owner_id, profile_id, conversation_id, source_turn_id)`。两者都不再包含 `profile_version`。

事务边界保持不变：PostgreSQL repository 在同一事务和 advisory lock 下执行“查找/创建 capture、写 candidate/review/memory/relation、完成 capture”；应用层进程锁只减少同进程竞争，数据库唯一约束才是跨进程权威。重复请求若 payload fingerprint 相同则复用/恢复原 capture；不同则返回稳定错误 `idempotency_conflict`。

失败后需要重新处理的同一事件可采用当前 Profile 版本，并更新该 capture 的版本、指纹、Prompt、schema 和模型审计字段。若 Profile ID、owner 或来源 payload 已改变，则 payload fingerprint 不同，不能借重试悄悄改变事件含义。

**Rejected alternatives:** 保留版本化唯一键会制造跨发布重复记忆；只依赖 Agent 去重无法覆盖网络重试和多进程服务；全局 `event_id` 唯一会错误地跨 owner 冲突。

### 3. 默认 Profile 属于认证主体，而非 Agent 本地默认值

`ConfiguredPrincipal` 和认证 claim 增加 `default_profile_id`，缺省为 `general-work`。认证完成后的 `RequestPrincipal` 携带该字段；capture/recall 工具接收可选 `profile_id`，为空时才使用主体默认值。服务启动时校验所有配置的默认 Profile 已注册，避免首次请求才发现部署错误。

Agent 的 `profile_id` 改为可选，默认不向 MCP 参数发送该字段。`MEMORY_HOOK_PROFILE_ID` 保留给需要同一 Token 临时选择不同策略的高级集成。owner、tenant、subject 和 client 身份仍全部来自服务端认证，Profile 路由不会扩大数据访问权限。

**Rejected alternatives:** 让 Agent 根据文本调用模型分类会增加延迟、成本和不可预测性；继续默认发送 `general-work` 无法做到 Token 即配置；用 URL path 区分 Profile 会复制服务端点并让部署配置膨胀。

### 4. 评测只依赖公开生产 Recall 服务

Recall case 在内存 repository 中建立与生产相同的 `MemoryRecord`，通过公开 `MemoryService.recall_memory` 执行并把返回 memory ID 映射回评测标签。评测不得导入 `_profile_relevance` 等私有函数。这样阈值、有效期、敏感/生命周期约束、关系扩展、token budget、去重和最终排序的变化都会体现在结果里。

内存 repository 是端口契约的确定性替身，不是另写一套 Recall 算法；在线模型只参与候选抽取和自动关系评测，Recall case 本身保持可重复。

**Rejected alternatives:** 复制打分逻辑会再次漂移；为评测启动 HTTP 服务会把传输和认证噪声混入算法回归，并显著降低单元反馈速度。

### 5. 候选硬上限下推到 repository

RecallService 通过 repository 端口传入 `limit`。PostgreSQL 在 owner、Profile、current、active、effective、可选类型/subject 条件之后，以 `observed_at DESC, memory_id` 稳定排序并应用 `LIMIT`；内存实现采用相同语义。上限由服务端配置提供，必须为正数，默认值在小规模原型下给出足够余量。

这是过载保护，不是最终检索架构：较老且低频的相关记忆可能被截断。文档会明确当单 owner/Profile 接近上限时，应进入后续 embedding/全文索引设计，而不是持续调大内存读取。

**Rejected alternatives:** 无界读取会随数据增长放大延迟和内存；仅在 Python 切片不能减少数据库传输；本阶段启用 `pg_trgm`、向量或外部搜索会引入部署扩展和新的质量评测范围。

## Dependency Direction and Transaction Boundaries

- `core.domain` 和 `core.application` 只依赖 `core.ports` 协议；策略指纹函数放在 Profile 端口/策略层，不能读取 Settings 或数据库。
- PostgreSQL 与 in-memory adapters 实现新的 bounded-query 和 capture 审计契约；MCP tools 只负责认证、Profile 解析、schema 映射和错误翻译。
- Agent 包只知道“Profile 可省略”，不知道服务端 Profile registry 或认证配置格式。
- 评测依赖公开应用服务和 in-memory adapter，不依赖私有排序函数或 PostgreSQL。
- 捕获的数据库事务边界不拆分；Profile 解析发生在进入事务之前，唯一约束和 advisory lock 在事务内最终仲裁。

## Risks / Trade-offs

- [历史数据库已存在跨版本重复 event] → 迁移不自动删除业务数据，而是让唯一约束创建失败；运维先审计并显式处理重复项。
- [候选上限截断很老但相关的记忆] → 默认值保守、稳定按最近观察时间选取、记录边界，并把索引化检索列为容量增长后的下一阶段。
- [Token 默认 Profile 配错导致行为与预期不符] → 服务启动即校验，配置示例显式展示，并在不记录内容的结构化日志中记录解析后的 Profile ID。
- [策略字段新增但指纹函数遗漏] → 内置 Profile 的指纹快照测试和代码评审清单共同约束；新增行为字段必须同步规范化器。
- [升级 v2 后旧 capture 的审计指纹未知] → 迁移为历史记录写入明确的 `legacy-unknown`，不伪造旧策略；新处理或恢复处理时写入真实指纹。

## Migration Plan

1. 部署前运行全量测试和严格 OpenSpec 校验，并检查历史 `event_id` 是否在同一 owner 内重复。
2. 运行 PostgreSQL migration：新增 `profile_fingerprint`，历史行置为 `legacy-unknown`；移除包含 Profile 版本的旧唯一约束，创建新逻辑唯一约束和部分唯一索引。
3. 部署服务端代码和更新后的静态认证配置。未提供 `default_profile_id` 的主体自动使用 `general-work`，可滚动兼容旧配置。
4. 再升级轻量 Agent。旧 Agent 仍显式发送 `general-work`，新 Agent 默认省略字段；两者可在迁移窗口共存。
5. 运行数据库 health、传输契约和 Recall 回归评测，确认捕获审计字段、默认路由和候选上限。

回滚代码前必须保留新列和更严格唯一约束；它们对旧代码是附加字段/更严格数据保护。若确需回滚约束，使用单独向前 migration 恢复旧索引，禁止删除已生成的 capture 或 memory。

## Open Questions

无。本变更之后的索引化语义检索、动态身份提供方和外部队列均作为独立提案评估。
