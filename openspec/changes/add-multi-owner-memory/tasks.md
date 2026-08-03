## 1. OpenSpec 变更提案

- [x] 1.1 创建 `openspec/changes/add-multi-owner-memory/` 目录与 `.openspec.yaml`。
- [x] 1.2 编写 `proposal.md`、`design.md`、`tasks.md` 和两个 spec delta。
- [x] 1.3 `openspec-cn validate add-multi-owner-memory --strict` 通过。

## 2. 清理冗余字段 last_verified_at

- [x] 2.1 新增 migration `0002_drop_last_verified_at.sql` 删除两表的 `last_verified_at` 列。
- [x] 2.2 `MemoryRevision`、`Candidate`（domain/capture.py）删 `last_verified_at` 字段。
- [x] 2.3 schemas.py 删该字段。
- [x] 2.4 recall_service 的 `_render_item` 删 `last_verified_at` 渲染行。
- [x] 2.5 PostgreSQL mapping.py 删该字段映射。
- [x] 2.6 in_memory adapter 删该字段相关逻辑。

## 3. 扩展身份模型支持团队归属

- [x] 3.1 `settings.py` 的 `ConfiguredPrincipal` 加 `team_ids: frozenset[str]`。
- [x] 3.2 新增 `derive_team_owner_key(tenant_id, team_id)` 函数。
- [x] 3.3 `auth.py` 的 `RequestPrincipal` 携带 `team_owner_ids`，`to_core()` 传入。
- [x] 3.4 `PrincipalContext` 加 `team_owner_ids` 和 `visible_owner_ids` property。

## 4. 实现召回合并个人 + 团队

- [x] 4.1 PostgreSQL `recall.py` 的 `base_conditions` 改 `owner_id = ANY(%s)`。
- [x] 4.2 PostgreSQL `repository.py` 的 `list_relations`、`find_current`、`list`、`get`、`get_history`、`list_reviews`、`get_review` 等 owner 过滤改集合。
- [x] 4.3 in_memory adapter 的 owner 过滤改 `in principal.visible_owner_ids`。
- [x] 4.4 `load_recall_evidence` 透传集合。

## 5. review 显式提升团队记忆

- [x] 5.1 `review_service` 的 `confirm` 加可选 `team_id` 参数，校验 `team_owner_ids` 包含它。
- [x] 5.2 写入时若 `team_id` 指定，owner_id 用团队 owner key。
- [x] 5.3 MCP 工具 `confirm_pending_memory` 加 `promote_to_team` 可选参数。
- [x] 5.4 `materializer.record` 和 `_evidence` 加 `owner_id` 覆盖参数。
- [x] 5.5 放宽 `_validate_record` 和 `validate_review_memory` 允许团队 owner 写入。

## 6. 测试

- [x] 6.1 新增多层召回测试：团队记忆可被团队成员召回，非成员不可见。
- [x] 6.2 review 提升测试：confirm 时 promote_to_team 写入团队 owner。
- [x] 6.3 review 权限测试：无权团队提升被拒绝。
- [x] 6.4 个人 + 团队记忆统一排序测试。
- [x] 6.5 删除 last_verified_at 相关测试断言。
- [x] 6.6 更新 static_verifier claims 断言含 team_ids。
- [x] 6.7 跑全量 pytest 通过。

## 7. 真实 DB 应用 migration

- [x] 7.1 开发库重建 schema（drop + 跑 0001 + 0002）。
- [x] 7.2 validate_schema 通过，契约测试通过。

## 8. 文档梳理

- [x] 8.1 design.md 清理残留 last_verified_at 描述，补多层记忆设计和团队 owner 派生。
- [x] 8.2 config.md 补 team_ids 配置说明和多层记忆小节。
- [x] 8.3 .env.example 补 team_ids 示例。

## 9. 验证

- [x] 9.1 ruff format + check 通过。
- [x] 9.2 pytest + evals + openspec validate 通过。
- [x] 9.3 PostgreSQL 契约测试通过（13 个，含 2 个新增多层记忆真实 DB 测试）。
- [x] 9.4 修复 `to_record` 用 `principal.owner_id` 查团队记忆 evidence 的 bug，改为 `row["owner_id"]`。
