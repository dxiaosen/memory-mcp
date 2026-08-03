## Why

当前 Recall 的应用层打分在中文投研场景下存在一个静默失效的信号通道：`_text_relevance` 使用 `re.compile(r"\w+", re.UNICODE)` 切词，而 Python 的 `\w` 在 Unicode 模式下会把没有空格分隔的 CJK 连续文本当作单个 token。于是 `看好新能源` 与 `锂电池前景` 的 word 集合交集为空，×0.25 的词重叠信号对无空格中文完全不起作用，中文召回实际只靠 trigram 字符二元组在支撑。与此同时，多个影响召回结果和渲染预算的数值是未对着评测校准过的硬编码常量，`_estimate_tokens` 的 `len/3` 对中文严重低估真实 token 数，导致 `rendered_context` 实际 token 数远超声明的 `token_budget`。

此外，敏感内容规则硬编码在代码里，投研场景中 `transaction_instruction` 规则可能把"买入格力电器"这类本应被捕获为研究偏好的正常文本整段 REDACTED 掉，直接吃掉本该保存的记忆；工具层 `except Exception` 把所有未识别异常统一映射为可重试的 `temporarily_unavailable`，让编程错误被当作临时故障反复重试；维护循环在 `has_more` 持续为 True 时只做 `asyncio.sleep(0)` 让出一次事件循环就续批，在异常持续不推进的场景下会形成不做退避的紧密循环。

当前离线评测 `recall_at_k=1.0`，因此本变更是预防性加固并为更大规模真实数据做准备，不是修复一个已坏的功能。

## What Changes

- 在 `core.domain` 引入可注入的中文分词能力，让 `_text_relevance` 的 word overlap 信号对无空格中文真正生效；分词器通过端口注入，`core.domain` 仍不直接依赖 jieba 等分词库。
- 把 Recall 打分中的硬编码魔数（relevance threshold、relation boost、profile hint boost、subject 精确命中加成）提取为命名常量，并在离线评测上校准；subject 精确命中加成从 `0.45` 下调到 `0.2`，避免 subject 命中但正文无关的记忆压过正文高度相关的记忆。
- 将 `_estimate_tokens` 从单一 `len/3` 改为按字符类别估算：CJK 字符按约 1 token/字，ASCII 按 1 token/4 字符，使中文为主的渲染预算不再严重低估。
- 让 `RegexSensitiveContentGuard` 的规则可由服务端配置注入，`MemoryServerSettings` 新增可选敏感规则配置项；默认规则集不变以保持现有安全评测基线，但投研部署可按需调整。
- 收紧工具层错误映射：未知异常记为 ERROR 级且默认不可重试（fail fast），只在明确临时性错误时才标记 `retryable=True`。
- 为维护循环增加连续 `has_more` 软上限，持续积压超过阈值时插入短延迟退避，避免紧密循环持续占用数据库连接。
- 将 9 个增量 migration 文件合并为单个 schema 文件，并移除全部外键约束；引用完整性由应用层事务和 advisory lock 保证，不再依赖数据库外键。
- 更新设计、配置、评测、测试文档，并在 openspec 变更中记录依赖方向、被拒替代方案和迁移边界。

## Capabilities

### New Capabilities

- `chinese-recall-precision`: 规定可注入的中文分词、word overlap 信号对 CJK 文本的有效性、打分常量的命名与校准记录，以及按字符类别的 token 估算合同。
- `recall-robustness-hardening`: 规定敏感规则的可配置注入、工具层未知异常的 fail-fast 映射，以及维护循环连续续批的退避合同。

### Modified Capabilities

无。主规范尚未归档，本变更以独立增量能力描述新增行为，归档时再与既有召回相关变更统一同步。

## Impact

- Server Core：`core.domain.lifecycle` 新增分词协议与默认实现委托，`core.application.recall_service` 改用分词并调整常量，`core.composition` 注入分词器实现。
- Server：`core.adapters.sensitive` 支持配置注入，`settings.py` 新增敏感规则配置项，`tools/shared.py` 收紧异常映射，`app.py` 维护循环增加退避。
- PostgreSQL：9 个增量 migration 合并为单个 `0001_memory_schema.sql`；全部外键约束移除，CHECK/UNIQUE/索引保留。开发阶段已清空历史 migration 记录，因此可安全合并而不产生 checksum 冲突。
- 依赖：Server `pyproject.toml` 新增 `jieba` 运行依赖；Agent 包不引入分词依赖，召回打分只在 Server 侧执行。
- 评测：离线确定性评测继续作为召回质量基线，记录改造前后指标；不新增模型热路径调用。
- 非目标：不引入 Embedding、pgvector、向量库或 LLM rerank；不改变 MCP 工具签名；不破坏确定性评测可复现性；不做 cross-encoder 二阶段重排。
