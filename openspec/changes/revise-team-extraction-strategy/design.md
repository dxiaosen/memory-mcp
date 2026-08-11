# 设计决策

## 决策 A：回退主题级归并

**选项**：(A) 保留改进 3；(B) 回退；(C) 换成"按类型差异化阈值"。

**选 B**。投研场景下 thesis/risk 已被 `memory_type` 分组天然隔离，主题归并解决的跨类型同主题
场景大多不存在。主题 Jaccard 0.25 在中文单字分词下覆盖有限（"毛利率下行风险" vs "毛利空间收窄威胁"
Jaccard=0 仍会漏），且引入合成 subject、均值 embedding、两遍 `assigned_ids` 归并等非自然产物。
C 方案（差异化阈值）更轻，但本轮不引入新配置维度，先回退到 embedding-only 的干净基线。

## 决策 B：幂等扩到 confirmed

原幂等只查 `status='pending'`，导致一条共识被 confirmed 后，成员继续写同样东西会重新聚簇、
产出新 pending，确认时撞 `SubjectScopeConflictError`（团队 owner 已有 active memory 占槽位），
但垃圾 pending 已留下。扩到 `status IN ('pending','confirmed')` 在候选写入前堵住，无需 schema 变更。

## 决策 C：候选 embedding 取簇中心

`cluster[0]` 随 `ORDER BY` 和成员写新东西而变，导致幂等比对的 embedding 不稳定。取簇内成员均值
（`average_embedding`）代表性强且稳定。复用已有 `average_embedding` 纯函数，`_embedding_param`
已能处理 tuple，无需新增适配。

## 决策 D：弱方向校验只查 business_progress

领域里无强结构化方向信号：`assertion_kind` 区分"谁说的"不是"站什么立场"；`direction_cues` 只用于
关系抽取；`business_progress` 的 `resolved`/`invalidated` 是唯一对立对（且 capture_guidance 要求
仅显式标立场时才填，多数为空）。故弱校验：簇内同时出现 resolved/invalidated 时不并簇，都为空则放行。
对立语义跨场景通用（状态机级对立，非投研专属），写在通用 Core 的 helper 里符合自包含约束。
不做 LLM 方向判断——覆盖有限但零误判。
