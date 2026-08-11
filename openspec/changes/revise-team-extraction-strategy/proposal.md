# 修订团队提取策略

## 背景

`improve-team-extraction-strategy` 引入了三项改进：确定性平局打破、分歧摘要、主题级归并。
落地后回看，主题级归并（改进 3）复杂度高、收益存疑：

- thesis 与 risk 已被 `memory_type` 分组天然隔离，主题归并解决的"跨类型同主题"场景大多不存在；
- 主题 Jaccard 0.25 在中文单字分词下覆盖有限（"毛利率下行风险" vs "毛利空间收窄威胁" Jaccard=0），
  并不比 embedding 余弦好到哪去；
- 引入合成 subject（`team:topic:` 前缀）、均值 embedding、`assigned_ids` 两遍归并等非自然产物，
  实现时还撞了"单例贪心簇算已分配"的坑。

同时审查暴露三个更实际的准确性缺口：

1. 候选级幂等只查 `status='pending'`：一条共识被 confirmed 后，成员继续写同样东西会再次聚簇、
   产出新 pending，确认时才撞 `SubjectScopeConflictError`，但垃圾 pending 已留下。
2. embedding 簇用 `cluster[0]` 原始向量做候选 embedding：代表性弱，且随成员写新东西/排序变化而漂移，
   使幂等比对的 embedding 不稳定。
3. 无方向校验：embedding 余弦 ≥ 0.70 可能把"看好毛利率提升"与"担忧毛利率下行"并成一条"团队共识"，
  实为团队分歧。领域里 `business_progress` 的 `resolved`/`invalidated` 是唯一结构化对立信号
  （`assertion_kind` 区分"谁说的"不是"站什么立场"；`direction_cues` 只用于关系抽取）。

## 提议

- **回退主题级归并**：移除 `team_extraction_topic_groups` Profile 扩展点及两套适配器的二轮主题聚类。
- **幂等扩到 confirmed**：候选级幂等查询从 `status='pending'` 扩到 `status IN ('pending','confirmed')`。
- **候选 embedding 取簇中心**：用簇内成员均值替代 `cluster[0]`，提高代表性、稳定幂等比对。
- **弱方向校验**：簇内同时出现 `resolved` 与 `invalidated` 的对立 `business_progress` 时丢弃该簇。
  只拦截显式标了立场的少数情况（`business_progress` 多数为空时放行），覆盖有限但零误判，不引入 LLM 判断。

不做：单链聚类传递性、阈值尺度统一、置信度下限、失效链路——留后续。

## 影响

- `memory_reviews` 表无 schema 变更。
- Profile 指纹因移除 `team_extraction_topic_groups` payload 键而变化，需重算内置 Profile 指纹。
- 现有 `improve-team-extraction-strategy` 变更的主题归并需求被本变更 REMOVED。
