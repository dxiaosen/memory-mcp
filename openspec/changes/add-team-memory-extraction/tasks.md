## 1. DB schema

- [x] 1.1 `0001_memory_schema.sql` 新增 `memory_team_extraction_runs` 表。
- [x] 1.2 重建 schema。

## 2. Domain + Ports

- [ ] 2.1 新增 `TeamMemoryCandidate` domain type。实现未引入此类型；聚类在 DB 侧 SQL 完成，候选直接落为团队 pending review，无需独立 domain 候选对象。
- [ ] 2.2 `MemoryRepository` 端口新增 `find_team_candidates` 和 `create_team_review` 方法。实现合并为单一 `extract_team_common_memories` 方法（SQL 侧聚类并直接写团队 pending review），未拆成两个方法。
- [x] 2.3 新增 `TeamExtractionResult` domain type（含 member/memory/cluster/candidate_count）。
- [x] 2.4 导出新类型（`core.domain.__init__` 与 `core.__init__` 已导出 `TeamExtractionResult`）。

## 3. TeamExtractionService

- [x] 3.1 新建 `application/team_extraction_service.py`。
- [x] 3.2 聚类算法：embedding 余弦相似度 + 贪心聚类（在 PostgreSQL `extract_team_common_memories` 内完成，非 Python 侧）。
- [x] 3.3 候选构造：subject/content/confidence 从簇内聚合（DB 侧）。
- [x] 3.4 幂等：检查已有 pending 不重复创建（DB 侧 `memory_team_extraction_runs` 记录）。

## 4. PostgreSQL adapter

- [x] 4.1 `repository.py` 实现 `extract_team_common_memories`（查成员个人记忆 + embedding 聚类 + 写团队 pending review，一次 SQL 完成）。
- [x] 4.2 `mapping.py` 加映射（结果集映射到 `TeamExtractionResult`）。
- [x] 4.3 in_memory adapter 同步实现（`InMemoryMemoryRepository.extract_team_common_memories` 已实现，含 embedding 聚类、幂等与团队 pending 写入，语义对齐 PostgreSQL 版本）。

## 5. 集成

- [x] 5.1 `app.py` 加第二个 maintenance runner（team extraction，`_run_team_extraction_loop`）。
- [x] 5.2 `settings.py` 加 team extraction 配置项（interval/similarity_threshold/min_cluster_size）。
- [x] 5.3 `app.py` `_create_team_extraction_service` 构造 TeamExtractionService（非 composition.py）。
- [ ] 5.4 `/health` 加 team_extraction 子状态（当前 `/health` 只暴露 maintenance health，无 team extraction 子状态）。

## 6. 测试

- [x] 6.1 单元测试：聚类算法 + 幂等（`tests/core/test_team_extraction.py` 覆盖聚类、幂等、阈值、非成员隔离、空成员）。
- [x] 6.2 in_memory 端到端：多成员写相似内容 → 提取 → pending（`test_team_extraction_service_run_once_collects_results` 验证服务层；confirm 复用既有 review 路径）。
- [ ] 6.3 真实 DB 契约测试（需显式 PostgreSQL）。
- [x] 6.4 跑全量 pytest + evals（201 passed）。

## 7. 文档

- [x] 7.1 `design.md` 加团队提取章节（§5.5）。
- [x] 7.2 `config.md` 加 team extraction 配置项（§3.6）。
