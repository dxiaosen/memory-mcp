## 上下文与选项

### 选项 A（采纳）：确定性选择 + 分歧摘要 + 主题归并（不合成）

- 簇内 subject/content 用确定性纯函数选择（频次优先 + 字典序兜底）。
- 分歧少数视角在 `save_rationale` 保留摘要，不丢失信息。
- 主题归并使措辞不同但主题相同的记忆也能产出候选。

### 选项 B（否决）：LLM 合成团队共识 content

- 提取阶段调模型把簇内多条 content 合成一条团队共识。
- 否决理由：违反"不虚构能力"铁律；引入合成幻觉风险；提取阶段本应快、不该再调模型；
  合成结果人审阅时无法分辨哪些是原文哪些是模型编造。原文保留 + 分歧摘要给人审阅更安全。

### 选项 C（否决）：调低全局 embedding 阈值到 0.5 覆盖风险聚合

- 把全局 similarity_threshold 从 0.70 调到 0.5，让措辞不同的风险链也能聚 embedding 簇。
- 否决理由：污染所有类型聚类（偏好/事实也会被错误归簇）；风险聚合是特定类型的问题，
  不该用全局降阈值解决；主题归并是更精准的机制。

## 决策

### 为什么主题归并走 Profile 扩展点而非 Core 硬编码

"哪些 memory_type 算同主题"（如 risk 与 thesis 表达同一经营质量关切）是业务词义判断，
属于场景边界（架构铁律 3）。通用 Core 不含投研词义，只提供"按声明的 topic_groups 做主题归并"
的机制。投研 Profile 声明 `business_quality` 组（risk + thesis）；不声明的 Profile 走纯
embedding 聚类，行为不变。这与 `timeline_relation_types`、`expiry_derivations` 等可选 Profile
属性的扩展模式一致。

### 为什么 subject 选择用频次优先 + 字典序兜底

- 频次优先：多个成员写了相同 subject 说明这是真共性，比单一 subject 更能代表团队共识。
- 字典序兜底：平局时取字典序最小，跨进程可复现，不依赖 set 哈希顺序（原 `max(set, key=count)`
  的平局兜底是非确定性的）。
- 不用"取最长 content 对应的 subject"：content 长度仍可能因换行/标点漂移，不如频次稳定。

### 为什么 content 选择用频次 → 长度 → 字典序

- 频次优先：同 subject 下相同 content 是强共性信号。
- 长度次之：保留信息量，更长 content 通常覆盖更完整。
- 字典序末位兜底：使结果可复现。

### 为什么主题簇用合成 subject 与均值 embedding

- subject 合成 `team:topic:{组名}:{关键词}`：与 embedding 簇的原始 subject 区分，避免幂等
  检查（同 subject+type 不重复）误判主题簇与 embedding 簇重复。关键词取簇内 subject 分词
  后频次最高 token，代表主题核心。
- embedding 取簇内成员均值：主题簇无单一代表 embedding，均值是该主题空间中心点的合理
  近似；用于幂等的语义去重检查（距离 < 0.05）。

### 为什么分歧摘要在 save_rationale 而非新字段

- `save_rationale` 已是自由文本字段，承载提取理由。
- 新增字段会改 schema（违反"无 schema 变化"目标）与 Candidate domain 结构。
- 分歧摘要是给人审阅的辅助信息，不参与结构化判断，放 rationale 最合适。
- 上线前随整体脱敏集收紧时，rationale 里的 content 前缀引用一并脱敏。

### assigned_ids 只跟踪有效簇，不跟踪单例贪心簇

贪心聚类总是把每条记忆至少放入一个簇（作为种子），即使相似度不达阈值也会形成单例簇。
若 assigned_ids 跟踪所有贪心簇成员，则所有记忆都被"独占"为单例簇，主题归并永远拿不到
候选。因此 assigned_ids 只跟踪"有效 embedding 簇"（满足 min_cluster_size 且 >= 2 个不同
成员）的成员，让未达 embedding 阈值的记忆有机会进入主题归并。

## 约束

- 主题归并的 Jaccard 阈值 0.25 与"共享至少一个有意义 token"对齐；太低会误并无关主题，
  太高会漏并同主题不同措辞。投研场景 0.25 是经验值，可通过 Profile 调整（目前全局常量）。
- 分歧摘要引用长度 40 字符：够展示少数视角要点，不把整段 content 搬进 rationale。
- 主题簇 embedding 均值是 best-effort 近似，不追求向量精度（主题归并本质是 best-effort）。
