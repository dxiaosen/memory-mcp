## 1. OpenSpec 变更提案

- [x] 1.1 创建 `openspec/changes/harden-recall-chinese-tokenization/` 目录与 `.openspec.yaml`。
- [x] 1.2 编写 `proposal.md`：Why、What Changes、Capabilities、Impact、Non-goals。
- [x] 1.3 编写 `design.md`：Context、Decisions、Rejected alternatives、Migration、Risks。
- [x] 1.4 编写 `tasks.md`（本文件）。
- [x] 1.5 运行 `openspec-cn validate harden-recall-chinese-tokenization --strict` 通过。

## 2. 召回中文分词与打分校准

- [x] 2.1 在 `core/domain/lifecycle.py` 定义 `MemoryTokenizer` 协议和 `SimpleTokenizer` 兜底实现，新增 `tokenize_memory_text` 函数。
- [x] 2.2 在 `core/adapters/` 新增 `JiebaTokenizer` 实现（依赖 jieba）。
- [x] 2.3 `RecallService` 构造函数新增可选 `tokenizer` 参数，`_text_relevance` 改用 `tokenize_memory_text`。
- [x] 2.4 `composition.create_memory_service` 注入 `JiebaTokenizer`。
- [x] 2.5 把 subject 精确命中加成提取为 `_SUBJECT_EXACT_MATCH_BOOST` 常量并从 `0.45` 下调到 `0.2`。
- [x] 2.6 Server `pyproject.toml` 新增 `jieba` 依赖并更新 lockfile。
- [x] 2.7 跑离线评测，记录改造前后 `recall_at_k` 和各类别 pass_rate，确认不回退。

## 3. token 估算修复

- [x] 3.1 `_estimate_tokens` 改为按 CJK / 非 CJK 字符类别估算（CJK 1 token/字，其他 1 token/4 字符）。
- [x] 3.2 补充 token 估算的单元测试（纯中文、纯英文、中英混合）。

## 4. 敏感规则可配置化

- [x] 4.1 `MemoryServerSettings` 新增可选 `sensitive_rules` 配置项（JSON 数组，每项 `{"category", "pattern"}`）。
- [x] 4.2 `app.py` 解析 settings 的敏感规则并通过 `RegexSensitiveContentGuard.from_config` 注入；无配置时用默认规则。
- [x] 4.3 补充敏感规则可配置的测试（注入自定义规则、无配置回退默认、非法正则拒绝）。
- [x] 4.4 `server/.env.example` 增加敏感规则配置示例与注释。

## 5. 宽泛异常映射修复

- [x] 5.1 `tools/shared.py` 的 `_map_error` 把未知异常默认改为 `retryable=False`；明确临时性异常（`OSError`、`TimeoutError`、`asyncio.TimeoutError`）才 `retryable=True`。
- [x] 5.2 `_error_response` 日志级别按已知边界错误（WARNING）与未知异常（ERROR）区分。
- [x] 5.3 补充异常映射的测试（未知异常 fail fast、临时异常可重试、ValueError 映射）。

## 6. 维护循环退避

- [x] 6.1 `app.py` 的 `_run_maintenance_loop` 增加连续 `has_more` 计数器，超过软上限时插入短延迟。
- [x] 6.2 软上限与短延迟作为命名常量（`_MAINTENANCE_HAS_MORE_SOFT_LIMIT=8`、`_MAINTENANCE_HAS_MORE_BACKOFF_SECONDS=1`）。
- [x] 6.3 补充维护循环退避的测试。

## 7. 测试与评测

- [x] 7.1 补充分词相关测试：无空格中文的 word overlap、混合中英文、分词器注入、纯标点丢弃。
- [x] 7.2 跑 `.venv/bin/python -m pytest -q` 全量通过（仅一个与本次无关的预存在失败）。
- [x] 7.3 跑 `.venv/bin/python -m evals.runner` 离线评测通过且 `recall_at_k` 不回退。

## 8. 文档更新

- [x] 8.1 `docs/design.md` 第 13 章：更新召回打分信号说明（加中文分词、改 token 估算描述、改魔数口径与 subject boost 值）。
- [x] 8.2 `docs/config.md`：新增 `MEMORY_MCP_SENSITIVE_RULES` 配置项；维护退避说明。
- [x] 8.3 `docs/evaluation.md`：记录分词改造前后的召回指标对比。
- [ ] 8.4 `docs/testing.md`：补充分词、token 估算、异常映射、维护退避的测试说明（如需要）。

## 9. 验证与收尾

- [x] 9.1 `uv run ruff format --check .` 通过。
- [x] 9.2 `uv run ruff check .` 通过。
- [x] 9.3 `.venv/bin/python -m pytest -q` 全量通过（仅一个与本次无关的预存在失败）。
- [x] 9.4 `.venv/bin/python -m evals.runner` 通过。
- [x] 9.5 `openspec-cn validate harden-recall-chinese-tokenization --strict` 通过。
- [x] 9.6 最终结构、配置、依赖和文档一致性复核。

## 10. Schema 合并与外键移除（附加）

- [x] 10.1 将 9 个 migration 文件（0001-0009）合并为单个 `0001_memory_schema.sql`，折叠所有中间 ALTER/RENAME 为最终表结构。
- [x] 10.2 移除全部外键约束（45 个 FK 列），保留 CHECK、UNIQUE 和索引；引用完整性由应用层事务和 advisory lock 保证。
- [x] 10.3 更新 `test_postgresql_contract.py` 的 migration 不变量测试，适配单文件 + 无外键。
- [x] 10.4 将 `_truncate_memory_tables` / `_truncate` 的 `TRUNCATE ... CASCADE` 改为显式清空全部表（无外键后 CASCADE 不再级联）。
- [x] 10.5 修复 `test_agent_environment_template` 测试的环境变量隔离。
- [x] 10.6 在开发库重建 schema（drop + 跑新 migration），确认 validate_schema 通过且外键数为 0。

