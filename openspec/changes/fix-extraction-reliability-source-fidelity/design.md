# 设计决策

## D1：两级空白归一化（whitespace + compact），不改写字符

**决策**：`_normalize_whitespace = " ".join(text.split())`、`_normalize_compact = "".join(text.split())`。
先 whitespace containment，失败再 compact containment。不复用 `normalize_memory_text`（其做
NFKC+casefold，会改写字符）。

**理由**：recommend.md §1 指出模型常把换行**删除**（「较高毛利率，\n可能」->「较高毛利率，可能」），
单空格归一后原文为「较高毛利率， 可能」（逗号后空格），表达式为「较高毛利率，可能」（无空格），
whitespace 级不匹配。compact 移除全部空白后两者一致。标点/数字/字符不动，因此模型改写、增标点、
拼接独立 bullet（bullet `-` 标记等非空白字符保留）仍判不匹配（§4 严格性）。

## D2：原子化与来源保真靠 prompt，不改 DTO

**决策**：Candidate/Evidence 单 `source_expression` 是对外 DTO，不改。靠 prompt 引导原子化、
事实完整支撑、关系语义不入事实 content、research_preference 用户专属、external_fact 来源优先。

**理由**：约束「MCP DTO 向后兼容」「Core 不含投研词义」。research_preference 不在 Core 硬编码
（违反 Profile 边界铁律），靠 prompt + §4 回声丢弃 + 既有 non_user_source PENDING 兜底。

## D3：抽取重试在 capture_service 内，仅对 InvalidModelOutputError

**决策**：`_extract_candidates` 在 capture_service 内对 `extractor.extract()` 做有界重试（max=3），
仅捕获 `InvalidModelOutputError`（null/parse/schema/validation 等 recoverable 结构错误）。业务校验
（invalid_source_expression）在后续 candidate_processing 产生 discard，不抛异常、不重试。全失败才
向上抛 -> 既有 except 写 incomplete + invalid_candidate_output。

**理由**：recommend.md §3。capture_service 持有 capture_id 可记 attempt 事件；重试在同一 Capture 内、
处理前，不产生重复 Capture/Memory。非结构错误的瞬时故障（如网络）不在本重试范围，走既有
REPROCESS_REQUIRED 重投路径。

## D4：Assistant 回声丢弃，不建 Pending/Evidence

**决策**：`_is_assistant_restatement` 在生命周期段对 `source_role=assistant` 候选查同 subject+type
精确命中 + `_content_restates`（归一等价/包含），未命中且 Profile 配了 semantic_dedup_threshold 时
再用 `find_semantically_similar` 兜底。命中即 discard `assistant_restatement`。

**理由**：recommend.md §4。Assistant 复述已有 Memory 不应产生新 Pending 或当新 Evidence。用户本人
重述 source_role=USER 不触发本规则，走既有 duplicate/evidence。语义兜底复用既有去重基础设施，
threshold（如 0.85）即「高度重复」。

## D5：Recall 查询归一化在 recall_service，确定性无 LLM

**决策**：`_normalize_recall_query` 按句末标点/换行切子句，剔除命中指令模式的子句，保留实体子句，
空结果回退原文。在 recall 入口对 query.query 归一化后用于 search_text（embedding+lexical），
recall.input 记 normalized_query。不下调全局阈值。

**理由**：recommend.md §7。完整 Prompt 的操作/格式指令稀释 embedding；剔除后保留实体/主题提升
正查询命中率。负向查询（纯实体）无指令子句 -> 不变 -> 仍 0（保南美铜矿负向）。不增 LLM、不改过滤。
**风险**：纯指令行剥离对短自然语句的阈值提升有限，Case B 若仍 <0.18 可后续加 jieba 名词抽取增强。
