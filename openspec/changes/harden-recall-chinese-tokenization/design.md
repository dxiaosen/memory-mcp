## Context

当前 Server 的召回链路是：PostgreSQL 用 `pg_trgm` 在 owner/Profile/effective 边界内拉取词法候选 + 近期候选补齐（默认 70/30 配额、上限 500），RecallService 在 Python 侧用 `_text_relevance` 做确定性重排，再经关系加权、阈值过滤、token 预算裁剪后批量水合 Evidence。这条链路在离线评测上 `recall_at_k=1.0`，但存在几个静默失效或失准的环节，在更大规模真实数据上会暴露。

约束：`core.domain` 和 `core.application` 不得依赖 MCP、HTTP、数据库驱动、Agent SDK 或运行时 Settings；owner 只能来自服务端认证上下文；业务正文不进入 operational log；PostgreSQL 是唯一权威存储；召回打分不能依赖模型可用性；离线评测必须确定性可复现。

## Goals / Non-Goals

**Goals:**

- 让 `_text_relevance` 的 word overlap 信号对无空格中文真正生效。
- 把打分魔数提取为命名常量并记录校准依据，subject 精确命中不再压过正文相关度。
- token 估算对中文不再严重低估，渲染预算反映真实占用。
- 敏感规则可由部署配置注入，投研场景可调整而不改代码。
- 工具层未知异常 fail fast 而非被当作可重试临时故障。
- 维护循环在持续积压时不形成紧密循环。
- 离线评测 `recall_at_k` 不回退，并记录改造前后对比。

**Non-Goals:**

- 不引入 Embedding、pgvector、向量库或 LLM query expansion/rerank。
- 不改变 PostgreSQL schema 或新增 migration。
- 不改变 MCP 工具名称、参数签名或 owner 隔离边界。
- 不改默认敏感规则集（保持现有安全评测基线）。
- 不做 cross-encoder 二阶段重排。
- 不让分词进入 Agent 包；召回打分只在 Server 侧。

## Decisions

### 1. 分词器作为端口注入，core.domain 不直接依赖 jieba

在 `core.domain.lifecycle` 定义 `MemoryTokenizer` 协议（一个 `tokenize(text: str) -> tuple[str, ...]` 纯函数接口）和一个基于正则的 `SimpleTokenizer` 兜底实现（保留对 ASCII 空格分词的能力）。`normalize_memory_text` 旁新增 `tokenize_memory_text(value, tokenizer)` 函数，默认使用 `SimpleTokenizer`。

`RecallService` 构造函数新增可选 `tokenizer: MemoryTokenizer` 参数，`_text_relevance` 改用 `tokenize_memory_text` 而非 `re.compile(r"\w+").findall`。`composition.create_memory_service` 在组装时注入基于 jieba 的 `JiebaTokenizer`；`JiebaTokenizer` 实现放在 `core.adapters`（adapter 层可依赖外部库），不在 `core.domain`。

评测的 `evals/metrics.py` 通过 `create_memory_service` 间接获得 jieba 分词器，无需自行构造；离线评测仍确定性，因为 jieba 的精确模式（`jieba.lcut` 不开 HMM 新词发现时）对相同输入产出相同分词结果。

**Rejected alternatives:**
- 直接在 `core.domain` `import jieba`：违反 core 层零外部依赖约束。
- 用 PostgreSQL 侧 `zhparser` 扩展做中文 FTS：引入数据库扩展部署复杂度，且当前规模不需要。
- 改 `\w` 正则用 `re.compile(r"[一-鿿]+|\\w+", re.UNICODE)` 按字符切：单字切分对中文语义粒度过细，词重叠信号仍弱于真实分词。

### 2. 打分常量提取与校准

把 `recall_service.py` 的模块级魔数 `_RELEVANCE_THRESHOLD=0.18`、`_RELATION_BOOST=0.12`、`_PROFILE_HINT_BOOST=0.16` 保留为命名常量（已是），把 `_score_record` 内的 subject 精确命中加成 `0.45` 提取为 `_SUBJECT_EXACT_MATCH_BOOST` 常量并下调到 `0.2`。

下调理由：`_text_relevance` 上限 0.9，加 0.45 后 clamp 到 1.0，使一条 subject 精确命中但正文完全不相关的记忆拿到满分，排在所有正文高度相关的记忆之前。0.2 让 subject 命中仍是有效加成（0.9+0.2=1.1 clamp 到 1.0 仍为最高），但正文相关度低（如 0.3）的 subject 命中记忆拿到 0.5，不再压过正文相关度 0.7 的记忆。

校准方式：跑 `python -m evals.runner`（离线确定性）记录改造前后 `recall_at_k` 和各类别 pass_rate，写入 `docs/evaluation.md`。当前 `recall_at_k=1.0`，校准目标是"不回退"。

**Rejected alternatives:**
- 把 subject 精确命中作为独立排序键而非 base score 加成：会改变现有排序语义结构，改动过大，留作后续。
- 用模型学权重：破坏确定性。

### 3. token 估算按字符类别

`_estimate_tokens` 改为：统计 CJK 统一表意文字范围（U+4E00–U+9FFF 及兼容范围）字符数 `cjk_count`，非空格非 CJK 字符数 `other_count`，返回 `max(1, ceil(cjk_count + other_count / 4))`。CJK 中文约 1 token/字，ASCII 英文约 1 token/4 字符，混合文本更接近真实 tokenizer 输出。

当前 `len/3` 对纯中文（如 30 字 recall context）估算 10 token，实际 tokenizer 约 30 token，低估 3 倍，导致 `token_budget=1200` 实际塞入约 3600 token 的中文内容。

**Rejected alternatives:**
- 引入真实 tokenizer（tiktoken 等）：增加依赖和延迟，且不同 provider tokenizer 不一致；当前只需"不再严重低估"，不需精确。
- 统一用 `len/2`：对英文仍低估，对中文仍不够。

### 4. 敏感规则可配置注入

`RegexSensitiveContentGuard` 构造已接受 `rules` 参数。新增从 `MemoryServerSettings` 读取规则的能力：`MemoryServerSettings` 增加可选 `sensitive_rules: SecretStr | None`，值为 JSON 数组，每项是 `{"category": str, "pattern": str}`。`composition.create_memory_service` 在构造 guard 时若 settings 提供了规则则解析注入，否则用 `DEFAULT_SENSITIVE_RULES`。

默认规则集不变，保持现有 8 个安全 case 的 `pass_rate=1.0`。文档标注：投研场景的 `transaction_instruction` 规则可能误伤"买入格力电器"这类研究偏好，部署可通过配置调整，但调整敏感规则是安全决策，需在文档明示风险。

**Rejected alternatives:**
- 改默认规则集：可能破坏现有安全基线，且哪些规则该改需要单独的安全评估。
- 把规则放到 Profile：敏感内容是服务端安全边界，不是 Profile 业务配置，不应让 Profile 持有脱敏策略。
- 用 LLM 做敏感检测：破坏确定性且增加延迟。

### 5. 工具层未知异常 fail fast

`tools/shared.py` 的 `_map_error` 最后的 fallback 当前把所有未识别异常映射为 `TEMPORARILY_UNAVAILABLE` + `retryable=True`。改为：未知异常（非 `MemoryMcpBoundaryError`、非已知领域异常、非 `ValueError`）默认 `retryable=False`，日志级别为 ERROR。只有明确临时性异常（`OSError`、`TimeoutError`、`asyncio.TimeoutError`）才 `retryable=True`。

`_error_response` 的日志级别按错误类型区分：已知边界错误（`MemoryMcpBoundaryError` 及子类）记 WARNING，未知异常记 ERROR。

**Rejected alternatives:**
- 全部不可重试：过度保守，真正临时性故障（DB 短暂不可达）应可重试。
- 用异常基类枚举所有临时错误：维护成本高，当前只识别明确临时异常即可。

### 6. 维护循环连续续批退避

`app.py` 的 `_run_maintenance_loop` 在 `result.has_more` 为 True 时当前只做 `await asyncio.sleep(0)` 让出一次事件循环就续批。增加连续 `has_more` 计数器，当连续续批超过软上限（默认 8 次）时插入短延迟（默认 1 秒），避免在异常持续不推进的场景下形成紧密循环持续占用 DB 连接。任何一次 `has_more=False` 或异常都重置计数器。

软上限和短延迟作为命名常量，可测试。

**Rejected alternatives:**
- 固定每次续批都 sleep：增加正常积压清理的延迟。
- 用指数退避：维护是幂等的，不需要复杂退避策略，简单软上限足够。

## Dependency Direction and Transaction Boundaries

- `core.domain` 定义 `MemoryTokenizer` 协议和 `SimpleTokenizer`，不依赖任何外部库。
- `core.adapters` 提供 `JiebaTokenizer` 实现，可依赖 jieba。
- `core.application.recall_service` 只依赖 `core.domain` 的分词协议，不依赖 jieba。
- `core.composition` 在组装时注入分词器实现，是唯一知道 jieba 的 core 边界点。
- `server/settings.py` 和 `server/tools/shared.py` 的改动不影响 core 层依赖方向。
- 敏感规则注入发生在 `composition` 边界，`RegexSensitiveContentGuard` 本身不依赖 Settings 类型。
- schema 合并不改变应用层事务边界；外键移除后，引用完整性由应用层事务和 advisory lock 保证，`_insert_relation` 等操作仍显式查询端点存在性。

## Decisions

### 7. migration 合并并移除外键

将 9 个增量 migration 文件（`0001`-`0009`）折叠为单个 `0001_memory_schema.sql`。合并方式是以最终表结构为目标直接 `CREATE TABLE`，去掉中间的 `ALTER`/`RENAME`/`DROP CONSTRAINT`/`ADD COLUMN` 语句。开发阶段已清空 `memory_schema_migrations` 表（历史库没有 migration 记录），因此合并不产生 checksum 冲突。

移除全部外键约束（原 45 个 FK 列，涉及 `memory_items`、`memory_revisions`、`memory_evidence`、`memory_relations`、`memory_review_items`、`memory_capture_outcomes` 等表的复合外键）。保留所有 CHECK、UNIQUE 和索引。引用完整性继续由应用层保证：`commit_capture` 在事务内用 advisory lock 串行化，`_insert_relation` 显式 `SELECT ... FOR UPDATE OF i, r` 检查端点存在性和有效性，`replace`/`revoke`/`review` 操作都锁定目标行后再更新。

测试里的 `TRUNCATE TABLE memory_profiles CASCADE` 改为显式列出全部表，因为无外键后 CASCADE 不再级联清空。

**Rejected alternatives:**
- 保留 9 个文件只删外键：增量 migration 的 checksum 会全部改变，历史库无法通过 `validate_schema` 的 checksum 校验。
- 保留外键只合并文件：用户明确要求去外键。
- 用 `ON DELETE NO ACTION` 替代 `RESTRICT`：本质相同，仍依赖外键约束，不满足去外键要求。

## Risks / Trade-offs

- [jieba 分词结果依赖词典，词典更新可能改变分词] → 锁定 jieba 版本，离线评测记录具体版本；精确模式不开 HMM 时对相同输入确定性。
- [subject boost 下调可能让某些原本靠 subject 命中的 case 不再 top-1] → 用 eval 校准，当前 `recall_at_k` 关注是否命中而非排序位置，下调不应降低命中率。
- [token 估算上调导致单次召回塞入更少记忆] → 这是正确行为，原来低估导致超预算；`max_items=10` 仍是最终条数上限。
- [敏感规则配置错误导致漏拦截] → 默认规则集不变；配置敏感规则是部署级安全决策，文档明示风险。
- [未知异常 fail fast 可能中断原本靠重试恢复的请求] → 真正临时错误仍标记 retryable；fail fast 只针对编程错误，这些本来就不该重试。
- [维护退避延迟积压清理] → 软上限 8 次续批后才触发，正常积压（一两次续批）不受影响。
- [外键移除后应用层 bug 可能写入孤儿行] → 应用层所有写入路径都显式检查引用存在性并加行锁；CHECK/UNIQUE 约束仍防止非法状态；生产部署前用契约测试覆盖。
- [migration 合并后历史环境无法回滚到旧版本] → 开发阶段库已重建；生产环境应在正式发布前同步重建，不保留跨版本迁移路径。

## Migration Plan

1. 开发库重建 schema：drop `public` schema 后跑新 `0001_memory_schema.sql`，确认 `validate_schema` 通过且外键数为 0。
2. Server `pyproject.toml` 新增 `jieba` 运行依赖，`uv sync` 更新 lockfile。
3. 发布 Server 代码；Agent 包不受影响，不升级也兼容。
4. 跑离线评测记录改造前后指标；确认 `recall_at_k` 不回退。
5. 若需回滚 Agent，移除 jieba 依赖并恢复 `SimpleTokenizer` 兜底；召回退化为字符级 word overlap，但不会中断服务。Server 的 schema 合并不可回滚到 9 文件版本，因为开发库已重建。

## Open Questions

无阻塞问题。是否引入向量/embedding 由后续真实投研失败样本和容量数据决定，本变更明确不预设答案。
