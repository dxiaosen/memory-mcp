## 1. Profile 版本与审计

- [x] 1.1 实现稳定的 Profile 策略指纹，并为内置 Profile 增加版本—指纹校验
- [x] 1.2 将通用和投研 Profile 升级到 v2，补充静默策略漂移的回归测试
- [x] 1.3 在捕获领域模型、repository 映射和 PostgreSQL schema 中记录 `profile_fingerprint`

## 2. 跨版本捕获幂等

- [x] 2.1 从应用锁、repository 查询和 in-memory 唯一键中移除 `profile_version`
- [x] 2.2 新增 PostgreSQL migration，重建显式事件与兼容来源的逻辑唯一约束
- [x] 2.3 覆盖升级后重试、不同 payload 冲突、跨 owner 复用和失败恢复测试

## 3. 服务端默认 Profile 路由

- [x] 3.1 为认证配置与 RequestPrincipal 增加并校验 `default_profile_id`
- [x] 3.2 让 capture/recall 工具在未显式传入 Profile 时使用认证主体默认值
- [x] 3.3 让轻量 Agent 默认省略 Profile 参数，同时保留环境变量高级覆盖
- [x] 3.4 更新认证、工具 contract、Agent client 和部署配置测试

## 4. Recall 生产链路与查询边界

- [x] 4.1 将 Recall 评测改为通过公开 `MemoryService.recall_memory` 执行
- [x] 4.2 为 repository 的当前记忆查询增加稳定排序和正数 `limit` 契约
- [x] 4.3 增加服务端 Recall 候选上限配置并下推到 PostgreSQL 与 in-memory adapter
- [x] 4.4 覆盖真实召回空结果、候选截断和 owner/Profile 隔离测试

## 5. 文档、迁移与验收

- [x] 5.1 同步设计、配置、部署、Agent 接入、测试和评测文档，删除冲突描述
- [x] 5.2 运行 migration/health、ruff、全量 pytest、离线评测、构建与严格 OpenSpec 校验
- [x] 5.3 检查工作树和迁移回滚边界，记录未实现的索引化检索等后续项
