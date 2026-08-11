## 1. Core domain 纯函数

- [x] 1.1 新增 `core/domain/team_extraction_helpers.py`：`select_cluster_subject`/`select_cluster_content`/`format_divergence_rationale`/`topic_signature`/`topic_jaccard`/`average_embedding` 纯函数。
- [x] 1.2 `core/domain/__init__.py` 与 `core/__init__.py` 导出新函数。
- [x] 1.3 `tests/contract/test_dependency_boundaries.py` 确认新文件只导入 core 内模块（通过）。

## 2. Core ports 扩展点

- [x] 2.1 `ports/profiles.py`：`MemoryProfile` 协议加 `team_extraction_topic_groups` 可选属性。
- [x] 2.2 `_validate_optional_team_extraction_topic_groups` 校验函数 + `validate_registration` 调用。
- [x] 2.3 `_team_extraction_topic_groups_payload` + `profile_fingerprint` 纳入新属性。

## 3. 投研 Profile 与测试 Profile

- [x] 3.1 `profiles/investment_research.py`：声明 `business_quality` 组（risk + thesis）。
- [x] 3.2 `tests/support/fakes.py`：`TestMemoryProfile` 加可选字段（默认空，保持向后兼容）。
- [x] 3.3 `profiles/__init__.py`：更新内置 Profile 指纹（investment-research + general-work）。

## 4. PostgreSQL adapter

- [x] 4.1 `repository.py`：`extract_team_common_memories` 传入 `topic_groups`。
- [x] 4.2 `_extract_team_common` 重写字段选择（确定性纯函数）、加主题归并、assigned_ids 只跟踪有效簇。
- [x] 4.3 新增 `_topic_cluster_unassigned`/`_topic_cluster_group`/`_cluster_topic_group_name`/`_cluster_top_keyword`/`_cluster_mode_str`/`_embedding_param`/`_resolve_topic_groups`。
- [x] 4.4 主题簇 subject 合成、embedding 均值、rationale 主题归并标注。

## 5. in_memory adapter

- [x] 5.1 `in_memory.py`：`register_profile` 存储 `team_extraction_topic_groups`。
- [x] 5.2 `extract_team_common_memories` 重写字段选择 + 主题归并，assigned_ids 只跟踪有效簇。
- [x] 5.3 `_team_candidate_from_cluster` 接收 `save_rationale` 参数（不再内部硬编码）。
- [x] 5.4 `_cluster_mode` 改确定性（频次 + `str(value)` 字典序兜底）。
- [x] 5.5 新增 `_in_memory_topic_cluster_*` 与 `_cluster_mode_str` 辅助函数。

## 6. 文档

- [x] 6.1 `docs/design.md` §5.5：阈值 0.85 → 0.70 + 理由；补主题归并、确定性选择、分歧摘要说明。
- [x] 6.2 `docs/config.md` §3.6：补 `team_extraction_topic_groups` 说明。

## 7. 测试

- [x] 7.1 `tests/unit/test_team_extraction_helpers.py`：10 个纯函数单元测试（确定性选择、分歧摘要、主题签名/Jaccard、均值 embedding）。
- [x] 7.2 `tests/integration/test_team_extraction.py`：5 个集成测试（subject 确定性、分歧摘要保留、主题归并产出、无 topic_groups 走 embedding、主题归并幂等）。
- [x] 7.3 `uv run pytest tests/unit tests/integration -q` 全通过（284 passed, 2 skipped）。
- [x] 7.4 `uv run ruff check .` 全通过。
- [x] 7.5 `uv run pytest tests/contract/test_dependency_boundaries.py` 边界检查通过。

## 8. OpenSpec

- [x] 8.1 `proposal.md` / `specs/team-memory-extraction/spec.md` / `design.md` / `tasks.md`。
