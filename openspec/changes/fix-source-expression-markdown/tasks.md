# Tasks: source_expression Markdown 强调标记剥离问题修复

## 1. 第 4 级归一化（lifecycle 纯函数）

- [x] 在 `core/domain/lifecycle.py` 新增 `_MARKDOWN_EMPHASIS` 正则常量与 `_strip_markdown_emphasis` 辅助函数
  - 证据：`server/src/memory_mcp/core/domain/lifecycle.py` `_MARKDOWN_EMPHASIS = re.compile(r"[*_~`]+")`、`def _strip_markdown_emphasis(value: str) -> str: return _MARKDOWN_EMPHASIS.sub("", value)`
- [x] 在 `source_expression_matches` 增加第 4 层：剥离 Markdown 强调标记后 compact 比较
  - 证据：`lifecycle.py` `source_expression_matches` 末段 `stripped_expression = _strip_markdown_emphasis(source_expression); return normalize_compact(stripped_expression) in normalize_compact(_strip_markdown_emphasis(source))`
- [x] 更新 `source_expression_matches` docstring（三级 → 四级归一化）
  - 证据：docstring 已改为"逐字/空白归一/compact/剥离 Markdown 强调标记 compact 四级"

## 2. Prompt 逐字保留指令（backends）

- [x] 候选抽取 prompt section A 增加"逐字连续子串，保留 Markdown 强调标记"指令
  - 证据：`extraction/backends.py` 候选 prompt section A 含 "source_expression MUST be a verbatim contiguous substring of the original message: preserve every character including punctuation, digits, and Markdown emphasis marks (**, _, `, ~~) exactly as they appear; do not clean, paraphrase, or strip formatting."
- [x] 关系抽取 prompt 增加同款逐字保留指令
  - 证据：`extraction/backends.py` 关系 prompt 含相同逐字保留指令
- [x] 关系抽取 prompt 增加禁止用存储 memory content 作为 source_expression 指令
  - 证据：`extraction/backends.py` 关系 prompt 含 "Do not use an endpoint memory's stored content as source_expression--it must come from source_turn, not from memory."

## 3. 测试

- [x] 新增 `test_source_expression_with_stripped_markdown_emphasis_is_accepted`
  - 证据：`tests/integration/test_capture_service.py` 该测试用 `**库存周转天数有了硬天花板**` 原文 + 剥离标记的 source_expression 断言通过
- [x] 新增 `test_source_expression_with_parenthesized_markdown_link_is_accepted`
  - 证据：同文件该测试用含 `[文本](url)` 的原文断言通过
- [x] 新增 `test_source_expression_with_substantive_rewrite_still_rejected`
  - 证据：同文件该测试用 `**毛利率 41%**` 原文 + `毛利率达到了百分之四十一的高位` 断言仍被拒
- [x] 全量测试通过：`uv run pytest -q` → 347 passed, 13 skipped（12 skipped 属真实 PG 契约 + 1 环境相关）
  - 证据：本地运行结果
- [x] ruff 通过：`uv run ruff check .`
  - 证据：本地运行结果
- [x] pyright 通过：`uv run pyright`
  - 证据：本地运行结果
- [x] 确定性评测通过：`uv run python -m evals.runner --mode deterministic`
  - 证据：本地运行结果（deterministic 全绿）
