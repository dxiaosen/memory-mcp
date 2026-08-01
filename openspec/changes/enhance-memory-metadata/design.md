## Context

Memory Core 已经把稳定逻辑对象、revision 和 Evidence 分开，并用 PostgreSQL
维护 owner、current、history 和捕获幂等。当前缺口不是重新设计记忆对象，而是
MemoryRevision 只保存内容、认识论标签和生命周期状态；Candidate 的 confidence
在 auto-save 后丢失，Evidence 也只能定位到会话消息，不能表达投研文档引用。

该增强必须保持 Core 内向依赖、历史 migration checksum、旧 Hook payload 兼容、
owner-first 查询和禁止正文不落库边界。它为后续投研 Profile 提供通用元数据，但
不得在 Core 中出现投研类型或 `profile_id` 特判。

## Goals / Non-Goals

**Goals:**

- 让每个新 revision 保存提取置信度、验证状态、敏感级别和有效时间窗；
- 让 Evidence 能表达会话、工具、网页和文档来源，同时保留精确原文片段；
- 让 Profile 为每种 memory type 提供默认敏感级别和可选有效期；
- 让 recall 和 list 自动执行有效性边界并返回可解释元数据；
- 提供 owner-scoped 撤销能力并保留历史；
- 使用向前 migration 兼容现有数据和客户端。

**Non-Goals:**

- 自动事实核验、来源爬取或文档存储；
- 后台 scheduler、物理删除、suppression 或保留策略；
- 基于敏感级别的团队 ACL、生产 OAuth/OIDC 或数据库 RLS；
- 让模型自由决定可信身份、最终验证状态或降低敏感级别；
- 投研 memory types、关系图和混合检索；这些属于后续 Profile 变更。

## Decisions

### 1. 元数据属于 Revision，来源细节属于 Evidence

内容变化可能同时改变置信度、敏感性和有效期，因此以下字段位于
MemoryRevision：

```text
extraction_confidence?  # 模型是否可靠地提取了原文，不等同于事实为真
verification_status     # unverified/user_asserted/user_confirmed/source_verified
sensitivity_level       # public/internal/confidential/restricted
valid_from
valid_until?
last_verified_at?
```

现有 `observed_at` 继续表示服务观察到陈述的时间，不能与有效期或来源发布时间
混用。旧 revision 的 extraction confidence 保持 NULL，避免用 `1.0` 伪造历史
确定性；其他字段使用 `unverified/confidential/observed_at` 保守回填。

来源元数据位于 Evidence：

```text
source_type = conversation | tool | document | web
source_uri/title/publisher?
published_at/retrieved_at?
content_hash/citation_locator?
```

Evidence 仍必须关联 conversation、turn 和 source expression。URI 只是引用，服务
不抓取其内容，也不在其中接受凭据。

拒绝把所有字段放进无 schema 的 JSONB，因为这些字段参与校验、召回和对外契约，
需要明确类型、migration 和数据库约束。

### 2. 验证状态由程序派生，置信度保持原始含义

新记忆的 extraction confidence 直接保存经过 schema 校验的 Candidate confidence。
明确用户表达自动保存为 `user_asserted`；pending 被当前 owner 明确确认后为
`user_confirmed`；assistant/tool 候选默认 `unverified`。本变更不自动生成
`source_verified`，只保留后续可信来源适配器的合法状态。

这样不会把“模型很确信自己提取正确”误写为“外部事实已经证实”。

### 3. Profile 声明类型默认值，Core 统一实体化

增加不可变 `MemoryMetadataPolicy`，包含 default sensitivity 和可选
`validity_days`。MemoryProfile 通过 `metadata_policies` 按合法 memory type 提供
策略；Registry 要求 key 与 memory_types 一致并校验天数为正数。

CandidateProcessor 根据 source role、profile policy 和可信 observed time 构造最终
metadata。文本里的 normalized time 继续表达“下周”等业务时间，不能误作 revision
生效时间。模型不得提供验证状态；敏感级别不得低于 Profile 默认值。
当前 `general-work` 全部使用 `confidential` 且不自动过期，保持既有运行行为。

拒绝在 CandidateProcessor 中按 `investment-research` 或具体 memory type 写条件，
因为那会让 Core 随业务场景增长。

### 4. 有效时间窗在权威候选查询中执行

Repository 的普通 current 查询同时要求：

```text
lifecycle_status = active
valid_from <= now
valid_until IS NULL OR valid_until > now
```

因此尚未生效和已超过有效期的内容在进入应用层排序前即被排除。当前不需要后台
任务把状态枚举改为 expired；`expired` 继续用于显式状态或未来调度。判断时间由
应用 clock 传给 Repository，测试可以固定。

### 5. 撤销更新 current revision 的生命周期状态

新增 `revoke_memory(memory_id)`，要求 `memory:review`。Repository 在 owner 条件下
锁定 current revision，把 lifecycle status 更新为 revoked，并返回更新后的记录；
重复撤销幂等。Revision 仍保持 current，便于 owner 通过详情/history 查看，但
所有普通 list/recall 查询因为非 active 而排除它。

跨 owner ID 与不存在继续返回同一个 `memory_unavailable`。本变更不接受 owner、
任意状态值或物理删除参数。

### 6. 来源字段向后兼容加入完成轮次合同

TurnMessage/MCP MessageBlock 增加全部可选来源字段。未提供时，user/assistant 消息
派生为 conversation，tool 消息派生为 tool。所有字段进入 payload fingerprint，
因此同 event 用不同来源重放会安全地产生 idempotency conflict。

所有自由文本和 URI 在持久化前进入现有 SensitiveContentGuard。命中禁止规则的
候选仍整体 blocked；敏感级别只是允许保存内容的治理标签，不能绕过禁止规则。

## Risks / Trade-offs

- **[历史 confidence 未知]** → 使用 NULL 并在 DTO 中明确可选，不伪造高置信度。
- **[模型提供的时间不可靠]** → valid_from 只使用可信事件 observed time；文本中的
  normalized time 保持业务时间语义，默认有效期由 Profile 程序策略计算。
- **[URI 带查询凭据]** → 持久化前执行敏感检查；文档要求只提交无 Secret 的稳定
  canonical URI。
- **[读取时有效期过滤与状态枚举不同步]** → 对外 eligibility 以时间窗为准；未来
  scheduler 只做状态物化优化，不能改变时间边界。
- **[撤销可被误操作]** → 要求 review scope、owner 限定和幂等；保留历史且不物理
  删除，后续 correction 可以创建新 revision 恢复。
- **[Profile 接口扩展影响测试 Profile]** → 一次更新所有正式/测试实现，并由
  Registry 契约测试检查完整 key 集合。

## Migration Plan

1. 增加 `0004_memory_metadata.sql`，只追加列、约束和索引；追加
   `0005_metadata_rollback_compat.sql` 为 valid_from 设置数据库时间默认值，使旧版
   Server 在保留向前 schema 的短期回滚中仍可写入；
2. 先发布支持新旧 Hook payload 的 Server，并执行 migration；
3. 更新 DTO、Repository、Core、Profile 和测试；
4. 更新 Agent Client 类型，但不要求宿主提供新字段；
5. 验证旧记忆可 list/get/recall，新记忆完整保存 metadata；
6. 回滚应用时保留新增 nullable/default 列，不执行破坏性降级。

## Open Questions

无阻塞问题。`source_verified` 的可信验证器、敏感级别 ACL、自动 expired 状态物化
和 correction/delete 属于后续独立增强。
