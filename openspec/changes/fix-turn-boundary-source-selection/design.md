# 设计决策

## D1：Turn 边界由 Agent Host Adapter 负责，Server 不感知 transcript 结构

**决策**：`extract_document_messages` 增 `user_prompt` 参数，定位 transcript 中「最近一次
用户文本消息」作为当前轮次边界，只返回其后的条目。`hosts.py` 透传 `saved.prompt`（before_run
存下的当前用户输入）。

**理由**：recommend.md §1 明确「Turn 边界由 Agent Host Adapter 负责；Server 不感知 Claude Code
transcript 结构」。定位逻辑用 `normalize(user_prompt) in normalize(用户文本)` 容错 Claude Code
对 prompt 的轻微包装；找不到时回退到最后一条用户文本消息；无用户文本消息时返回全部（兼容既有
只含 tool_use/tool_result 的 transcript 用例）。这样第二轮只发当前 turn 的 tool/document，
不再重复第一轮。

## D2：source_expression 用空白归一化匹配，不做 NFKC/casefold

**决策**：新增 `_normalize_source_text` = `re.sub(r"\s+", " ", value).strip()`，用于校验
（`process`）与来源定位（`_source_metadata`）两处子串匹配。不复用 `normalize_memory_text`
（其做 NFKC + casefold，会改写字符）。

**理由**：recommend.md §3 明确 normalize 只允许统一换行/空白 + trim，「不要改写字符内容」。
casefold 会让 `AI` 与 `ai` 等价，可能造成跨大小写误匹配；NFKC 改写兼容字符。空白归一化既
放过「真实原文 + 仅空白差异」（跨行句），又保留对「拼接独立 bullet」的拒绝（bullet `-`
标记破坏连续性，归一后仍非子串，满足 §4 严格性）。

## D3：来源选中显式优先级 user > tool > assistant

**决策**：`_source_metadata` 选中逻辑由 `next(USER, matching[0])` 改为新增 `_select_source_message`
按 `(USER, TOOL, ASSISTANT)` 顺序取首条命中。

**理由**：recommend.md §2 优先级 `user explicit > tool/document original > assistant paraphrase`。
旧逻辑 `user > 首条` 在 tool 消息先于 assistant 时通常等价，但显式优先级更健壮，且让用户
thesis/risk/ongoing_research/research_preference 稳定绑定 user 来源，避免被 `non_user_source`
挡掉而无法 auto_save。

## D4：用户原文优先靠 prompt 引导 + §3 机制，不改 DTO

**决策**：extraction prompt 增引导--用户自身判断的 `source_expression` 取用户原文逐字片段而非
assistant 复述；并补原子化「单 span、不拼接多 bullet」。配合 §3 归一化匹配与 D3 优先级，
用户明确长期判断落到 `source_role=user`、`user_view`、`explicit`，满足准入即 auto_save。

**理由**：`source_expression` 是模型自报字段，Server 只能校验不能替模型选原文。prompt 引导
模型取用户原文是根本；`_source_metadata` 的 user 优先是兜底（同一表达式同时命中 user/assistant
时选 user）。不改 Candidate/Evidence DTO（约束：向后兼容）。

## D5：Team 去重在构造时按 team_owner_id 合并成员

**决策**：`TeamExtractionService.__init__` 存 `team_configs` 前用 `_dedup_team_configs` 按
`team_owner_id` 去重，`member_owner_ids` 取并集保序，`profile_id` 取首个。

**理由**：recommend.md §9 指出 `team_count=3` 实为 1 个 team--按成员展开产生同一 team owner。
batch 前去重避免同一团队被反复提取、计数虚高。并集成员保证不丢成员；取首个 profile_id（同
team 不同 profile 属配置异常，保守取首）。
