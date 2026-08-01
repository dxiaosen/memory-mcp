## Why

当前原型已经保存来源、观察时间和生命周期状态，但活跃记忆没有保留候选置信度，
也没有验证状态、敏感级别、有效时间窗和可引用的外部文档来源。导师课题要求这些
元数据能够随记忆进入跨会话召回，因此需要先在通用 Memory Core 中补齐，而不是
让后续投研 Profile 各自实现不兼容字段。

## What Changes

- 为每个 MemoryRevision 保存可选提取置信度、验证状态、敏感级别、有效起止时间和
  最近验证时间；历史数据使用诚实的未知/保守默认值。
- 为 Evidence 增加通用来源类型、URI、标题、发布者、发布时间、获取时间、内容
  摘要和引用位置；所有自由文本继续经过敏感检查。
- 由 MemoryProfile 按 memory type 声明默认敏感级别和默认有效期，Core 负责统一
  校验和实体化，不按具体 profile_id 分支。
- 召回必须排除未到生效时间、超过有效期、非 active/current 或禁止持久化的内容，
  并在结构化结果中返回置信度、验证状态、敏感级别和有效时间。
- 增加 owner-scoped 的记忆撤销工具；撤销保留历史和来源，但立即退出普通召回。
- 保持敏感级别与禁止持久化两条边界独立：命中凭据、真实持仓或交易指令等禁止
  规则的原文仍不得入库、响应或日志。
- 不增加后台过期任务、物理删除、团队 ACL、OAuth/OIDC 或向量索引。

## Capabilities

### New Capabilities

- `memory-metadata`: 定义跨 Profile 通用的置信度、验证、敏感级别、有效时间窗、
  来源引用、召回过滤和 owner-scoped 撤销行为。

### Modified Capabilities

无。现有通用 Core 变更尚未归档为主规范；本变更以独立能力补充其可观察合同。

## Impact

- 领域对象：MemoryRevision、Evidence、TurnMessage、Candidate、RecallResult。
- Profile 端口：每类记忆的默认 metadata policy。
- PostgreSQL：新增向前 migration；不修改历史 migration checksum。
- MCP：完成轮次消息块增加可选来源元数据，list/get/recall 返回新字段，并增加
  `revoke_memory`。
- Agent：现有 Hook 输入和默认流程保持兼容；新字段均为可选。
- 测试和文档：增加 metadata、有效期过滤、撤销、来源追溯、迁移和兼容性覆盖。
