## 1. DB schema

- [ ] 1.1 `0001_memory_schema.sql` 新增 `memory_team_extraction_runs` 表。
- [ ] 1.2 重建 schema。

## 2. Domain + Ports

- [ ] 2.1 新增 `TeamMemoryCandidate` domain type。
- [ ] 2.2 `MemoryRepository` 端口新增 `find_team_candidates` 和 `create_team_review` 方法。
- [ ] 2.3 新增 `TeamExtractionResult` domain type。
- [ ] 2.4 导出新类型。

## 3. TeamExtractionService

- [ ] 3.1 新建 `application/team_extraction_service.py`。
- [ ] 3.2 聚类算法：embedding 余弦相似度 + 贪心聚类。
- [ ] 3.3 候选构造：subject/content/confidence 从簇内聚合。
- [ ] 3.4 幂等：检查已有 pending 不重复创建。

## 4. PostgreSQL adapter

- [ ] 4.1 `repository.py` 实现 `find_team_candidates`（查成员个人记忆 + embedding）。
- [ ] 4.2 `repository.py` 实现 `create_team_review`（写团队 pending review）。
- [ ] 4.3 `mapping.py` 加映射。
- [ ] 4.4 in_memory adapter 同步实现。

## 5. 集成

- [ ] 5.1 `app.py` 加第二个 maintenance runner（team extraction）。
- [ ] 5.2 `settings.py` 加 team extraction 配置项。
- [ ] 5.3 `composition.py` 构造 TeamExtractionService。
- [ ] 5.4 `/health` 加 team_extraction 子状态。

## 6. 测试

- [ ] 6.1 单元测试：聚类算法 + 幂等。
- [ ] 6.2 in_memory 端到端：多成员写相似内容 → 提取 → pending → confirm。
- [ ] 6.3 真实 DB 契约测试。
- [ ] 6.4 跑全量 pytest + evals。

## 7. 文档

- [ ] 7.1 `design.md` 加团队提取章节。
- [ ] 7.2 `config.md` 加 team extraction 配置项。
