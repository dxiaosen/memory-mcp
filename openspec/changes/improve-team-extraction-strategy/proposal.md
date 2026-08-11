## Why

2026-08-11 团队提取实际日志暴露三个簇内字段聚合问题，根因是当前策略过于简单（取一条、
取最长、只看 embedding 相似度），丢失了团队应沉淀的高价值信号：

1. **subject 选择非确定性**：`max(set(subjects), key=subjects.count)` 在平局时回退到
   `set` 哈希顺序，跨进程/Python 版本不可复现。实际日志里两成员写了不同 subject（频次各 1），
   最终落库 subject 取决于 set 迭代序。
2. **content "取最长"丢失并行/分歧信息**：`max(content, key=len)` 只保留一条原文、丢弃另一条。
   两成员写了语义相似但视角不同的偏好（如成员A "毛利率+库存周转" vs 成员B "增长+盈利+库存"），
   差异化视角完全消失在团队候选里。
3. **缺少团队级风险聚合**：两成员各自写了相关但不同的风险链（如"库存积压→降价→毛利率反噬"
   vs "库存周转上升→收入增长可能造假"）。两者 embedding 相似度未必达 0.70 阈值，贪心聚类
   不归簇，团队层永远看不到"两成员都在担心库存×毛利率交叉风险"这一共识。

附带：`design.md` 写默认阈值 0.85，而代码（`team_extraction_service.py`/`settings.py`）默认 0.70，
三处不一致；0.70 才是对的（投研共性措辞不同是常态）。

## What Changes

- **确定性 subject/content 选择**：用 Core 纯函数按 `(频次 desc, 字典序 asc)` / `(频次 desc,
  长度 desc, 字典序 asc)` 稳定排序取首，替换非确定性 `max(set, key=count)` 与 `max(key=len)`。
- **分歧摘要保留少数视角**：当簇内存在与主 content 语义分叉的少数成员（Jaccard < 0.6），
  在 `save_rationale` 追加分歧摘要（引用成员 content 前 40 字符 + owner 标识）。提取阶段不
  做 LLM 合成——原文保留在个人记忆里、分歧摘要在 rationale 给人审阅。
- **主题级归并**：Profile 可选声明 `team_extraction_topic_groups`（组名 → 同组 memory_type 集合）；
  同组内、未被有效 embedding 簇纳入的记忆按 subject 关键词 Jaccard（>= 0.25）二次归并，
  使措辞不同但主题相同的记忆（如 risk 与 thesis）也能产出团队候选。主题簇 subject 合成
  （`team:topic:{组名}:{关键词}`）、embedding 取簇内成员均值，与 embedding 簇区分以避免幂等冲突。
- **阈值文档修正**：`design.md` 默认阈值 0.85 → 0.70，补理由。
- **通用 Core 不写死风险语义**："哪些类型算同主题"由 Profile 声明（场景边界），Core 只提供机制。
  投研 Profile 声明 `business_quality` 组（risk + thesis）。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `team-memory-extraction`：增加确定性 subject/content 选择、分歧摘要、主题级归并、Profile
  可选 `team_extraction_topic_groups` 属性、阈值默认值修正为 0.70。

## Impact

- Core：新增 `domain/team_extraction_helpers.py`（纯函数，两适配器共用）；`ports/profiles.py`
  加 `team_extraction_topic_groups` 可选属性 + 校验 + 指纹。
- Adapters：PG + in_memory 的 `extract_team_common_memories` 重写字段选择、加主题归并。
- Profile：投研 profile 声明 `business_quality` 主题组；TestMemoryProfile 加可选字段（默认空）。
- Docs：design.md §5.5 阈值 + 主题归并；config.md §3.6 主题组说明。
- Tests：10 个纯函数单元测试 + 5 个 in_memory 集成测试 + 边界检查。
- Schema：**无变化**（embedding 列复用、save_rationale 复用）。
- 非目标：不做 LLM 合成 content；不调低全局 embedding 阈值；不在 Core 硬编码风险语义。
