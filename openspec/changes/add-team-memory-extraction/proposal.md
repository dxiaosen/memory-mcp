## Why

当前团队记忆只能通过人工在 `confirm_pending_memory` 时传 `promote_to_team` 显式提升，系统不会自动发现"多个团队成员写了相似内容"的共性。投研团队的实际场景是：多个研究员写了相似的偏好/论点/证据，这些共性内容应自动沉淀为团队公共知识。当前没有自动提取机制，依赖人工逐条提升，无法规模化。

## What Changes

- 新增 `TeamExtractionService`，定时扫描每个团队的个人记忆，用 embedding 聚类发现共性内容。
- 聚类条件：同 memory_type + embedding 余弦相似度 >= 0.85 + 簇大小 >= 2（至少 2 个成员写了相似内容）。
- 提取出的共性候选写入 `memory_review_items`（owner = 团队 owner，status = pending），等团队成员人工确认后变团队记忆。
- 复用 maintenance runner 托管提取任务，独立间隔（默认每小时）。
- 新增 `memory_team_extraction_runs` 表记录每次提取运行，防重复。

## Capabilities

### New Capabilities

- `team-memory-extraction`: 规定 embedding 聚类、共性候选生成、pending review 写入、幂等和可观测性。

### Modified Capabilities

无。

## Impact

- DB：新增 `memory_team_extraction_runs` 表。
- Core：新增 `TeamExtractionService`；`MemoryRepository` 新增 team extraction 查询和写入端口。
- Application：聚类算法（embedding 余弦相似度）。
- Server：app.py 集成第二个维护 runner。
- Settings：新增 team extraction 配置（间隔/阈值/最小簇大小）。
- 依赖：需要向量召回（embedding 列已存在）。
- 非目标：不做自动确认（候选进 pending 等人工确认）；不做跨团队提取；不做实时提取。
