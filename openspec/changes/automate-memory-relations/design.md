## Context

`add-memory-relations` 已经实现 Profile 关系策略、`MemoryRelation`、Repository、PostgreSQL `0006`、显式 MCP 治理工具以及一跳关系召回，但其 Capture 决策明确不自动建边。当前 AfterRun 只调用 `capture_completed_turn`：候选抽取、准入和记忆/待确认项在服务端完成，Agent Host 不知道 memory ID，也不应承担投研关系规则。

自动关系跨越模型适配、Core port/application、CaptureWrite 和两个 Repository adapter。它必须保持现有依赖方向：Profile 只声明业务词汇；Core 不导入投研实现、LangChain、MCP 或 psycopg；模型输出永远是不可信建议；owner 只来自 `PrincipalContext`；捕获重放不能再次调用模型或产生重复关系。

## Goals / Non-Goals

**Goals:**

- 让启用关系策略的 Profile 在 AfterRun/Capture 内自动识别并保存高置信关系。
- 让 Agent 使用者继续只配置 MCP 地址、Token 和一次静态 Hook，不提供 memory ID 或关系设置。
- 只把 owned、同 Profile、current、active、effective 且本轮确定保存的记忆暴露为有界模型端点。
- 在通用 Core 中校验端点白名单、关系类型与方向、来源表达、显式程度和置信度。
- 让本轮新记忆和自动关系在同一 Repository 事务提交，并保持事件幂等。
- 复用候选抽取的 ChatModel、Provider、凭据和运行参数，但保持两个严格 schema/提示词独立演进。
- 没有关系策略的 Profile 完全跳过关系阶段。

**Non-Goals:**

- v1 不为关系增加独立待确认项；低置信、歧义或依赖 pending/blocked 端点的建议直接跳过。
- v1 不自动推断一条现有活动关系已不再成立；端点撤销/过期仍会自动使关系停止参与读取和召回，错误边由治理工具撤销。
- 不做实体消歧、完整知识图谱、跨 Profile/owner 关系、多跳推理或递归扩展。
- 不把 Capture 改成队列/worker，也不增加 Agent 端模型、服务端包或配置项。
- 不修改已经部署的 `0006_memory_relations.sql`，本变更不需要新表或 migration。

## Decisions

### 1. 自动关系属于服务端 Capture，而不是 Agent Hook

AfterRun 仍只提交完整轮次。`CaptureService` 在候选准入之后执行可选关系阶段，Agent 不读取 `link_memories`，也不保存 memory ID。业务关系词汇从本轮 Profile 的 `relation_policies` 获取，通用 Core 不按 `supports`、`threatens` 等名称分支。

**拒绝方案：**让 Codex/Claude Code 在 Hook 中再次调用 `link_memories`。Agent Host 无法可靠得到本轮事务尚未提交的 memory ID，还会复制服务端安全和幂等规则，并破坏轻量客户端边界。

### 2. 复用一个 ChatModel，使用独立的第二次结构化调用

组合根只创建一次 Provider ChatModel，再分别构造 `CandidateExtractor` 和 `RelationExtractor`。候选调用仍返回 `CandidateBatch`；关系调用返回独立 `RelationBatch`。关系调用只在以下条件同时成立时发生：

1. Profile 的 `relation_policies` 非空；
2. 脱敏轮次仍有可处理正文；
3. 端点目录中至少存在一组符合某项 policy 方向的不同记忆。

第二次调用虽然增加一次延迟和费用，但它能引用 CandidateProcessor 已经实体化的稳定 memory ID，并且不会让关系解析污染已有候选 schema。一个大 schema 用本地候选序号跨引用虽然少一次调用，但候选可能在准入阶段变为 pending、duplicate、replacement 或 blocked，序号很容易指向未提交实体，因此拒绝。

### 3. 模型只看到有界、脱敏、owner-scoped 端点目录

`RelationExtractionRequest` 包含脱敏轮次、Profile 版本、允许的关系规则和最多 40 个 `RelationEndpoint`：

- 本轮 `AUTO_SAVE` 形成的新 `MemoryRecord` 优先；
- 同 owner/Profile 的 current、active、effective 既有记忆按与轮次/subject 的确定性文本相关度排序后补足；
- 端点只包含 memory ID、memory type、subject 和 current content，不包含 owner、Token、Evidence URI 或其他身份字段；
- 只保留至少出现在某项关系策略 source/target 集合中的 memory type。

模型返回 source/target memory ID、relation type、原文中连续的 `source_expression`、`confidence` 和 `expression_basis`。Pydantic 使用 `extra="forbid"` 和最多 20 条建议。Core 不相信 UUID：只接受本次请求目录中的 ID，并重新验证 Profile 方向。

既有记忆的读取仍由 Repository 先按可信 owner/Profile/current/effective 过滤。v1 使用现有 `find_current` 后在应用层有界选择；模型输入始终有上限。后续数据量证明需要时可增加专用检索 port，而不改变关系抽取合同。

### 4. 采用保守且确定的自动准入

`AutomaticRelationPlanner` 只接受同时满足以下条件的建议：

- `source_expression` 是脱敏轮次中的精确连续子串；
- 存在结构化消息块时，`source_expression` 必须命中脱敏后的用户消息，不能只来自 Assistant/Tool；
- `expression_basis == explicit`；
- `confidence >= 0.90`；
- source/target 是目录中的不同 ID，且其 owner/Profile/状态均来自可信记录；
- `ProfileRegistry.validate_relation` 接受关系类型和有向端点类型；
- 同一批不存在相同 `(source, target, type)` 的冲突建议。

低置信、inferred/ambiguous、仅由 Assistant/Tool 表达、未知端点、非法方向和同批重复建议不写入。结构损坏、未知 ID、非法类型、伪造来源表达属于不可信模型合同错误，使捕获进入现有 `FAILED/invalid_candidate_output`；结构合法但未达到保守阈值属于正常跳过，不让整个轮次失败。

自动关系使用服务端 `id_factory` 和 `clock` 生成 `MemoryRelation(active)`。手工 `link_memories` 继续作为修复/治理能力；相同活动关系由 Repository 唯一语义收敛。

### 5. 关系与记忆在一个 CaptureWrite 原子提交

`CaptureWrite` 增加 `relations`。CandidateProcessor 不访问关系抽取器；CaptureService 组合候选处理结果和关系计划，然后只调用一次 `commit_capture`。

进程内 adapter 在复制的 records/relations 映射上校验和应用后一次替换；PostgreSQL adapter 在插入本轮 MemoryItem/Revision 后、写入 capture outcomes 前校验端点并 `INSERT ... ON CONFLICT ... DO NOTHING`。数据库现有复合外键、关系词汇外键、自环约束和活动唯一索引继续作为最后防线。

Capture 重放在进入模型前返回已提交 `CaptureResult`，因此不重复调用候选或关系模型。事务中途失败不会留下新记忆或关系。`0006` 已部署且 checksum 不可修改，本变更复用其结构。

### 6. 失败、日志和配置沿用现有合同

关系 schema/安全校验失败映射为 `invalid_candidate_output`；Provider/网络等意外异常映射为 `REPROCESS_REQUIRED/processing_interrupted`，下一次同事件可重做。默认 operational log 只记录 capture ID、关系数量、模型/schema 版本和稳定 ID，不记录轮次、subject、content、source expression、owner 或凭据；显式内容日志开关仍可在人工联调时输出脱敏后的关系建议。

关系抽取不增加环境变量。生产组合根从同一 `ExtractionSettings` 创建一个 ChatModel 和两个 extractor；测试或外部嵌入可以分别注入 fake extractor。若未注入 `RelationExtractor`，关系阶段安全跳过，用于兼容既有离线测试和自定义组装。

## Risks / Trade-offs

- **[启用投研关系后 AfterRun 增加一次模型延迟]** → 仅有合法端点组合时调用，严格限制 40 个端点和 20 条建议；后续通过评估决定是否合并调用或异步化。
- **[相关既有端点未进入 40 项目录]** → 本轮新记忆优先，既有记忆按确定性相关度选择；记录端点/建议/接受数量用于调优，不让模型输入无界增长。
- **[高置信模型仍可能建立错误边]** → 强制显式原文、白名单端点、Profile 方向和 0.90 阈值；保留撤销工具；后续用评估集决定是否引入关系待确认。
- **[自动关系没有独立证据表]** → v1 的来源由同一 capture/turn、运行日志和端点 Evidence 间接审计；若后续要求逐边出处/置信度历史，再以向前 migration 增加关系 provenance，绝不修改 `0006`。
- **[同一 ChatModel 对象生成两个结构化 wrapper 的 Provider 差异]** → factory 和 fake-model 契约测试验证两次 `with_structured_output`；如果某 Provider 不支持，启动/测试阶段失败而不是运行中静默降级。
- **[进程内手工 link 与 Capture 并发]** → relation 锁和活动唯一语义收敛；PostgreSQL 由部分唯一索引处理并发。

## Migration Plan

1. 先部署包含 `0006` 的现有关系底座；本变更没有新 migration。
2. 部署新 Server 代码并重启；Agent 包、Hook 配置、URL 和 Token 不变。
3. 用单一测试 owner 验证“已有 thesis → 新 evidence 明确支持 → 自动出现 supports → 下轮召回展示关系”。
4. 检查 `memory.capture.relations_planned` 和完成计数，不开启正文日志即可确认自动阶段。
5. 若模型质量不满足要求，回滚 Server 代码即可；已经建立的合法关系仍可由旧关系底座读取/撤销，不影响记忆数据。

## Open Questions

无阻塞问题。关系待确认、自动语义撤销、逐边 provenance 和专用关系候选检索属于质量评估后的独立增量。
