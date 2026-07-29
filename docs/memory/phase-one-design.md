# Memory Core 阶段一详细设计与代码导读

## 1. 文档目的

本文只解释已经实现并通过验收的阶段一，不提前描述阶段二、三的具体实现。
阅读完本文后，应该能够回答：

1. 阶段一解决了什么问题，为什么还不能在聊天中自动记忆；
2. 一条记忆由哪些对象组成，为什么要拆成 Item、Revision 和 Evidence；
3. 一次创建或查询会经过哪些代码；
4. 场景如何扩展，Core 为什么不包含投资等具体业务类型；
5. 用户隔离在哪几层生效；
6. SQLite 表、约束、迁移和健康检查分别负责什么；
7. 后续阶段应该从哪里继续，而不破坏现有边界。

对应 OpenSpec 变更为 `add-general-memory-core`，阶段一任务 1.1～1.9 已全部完成。

## 2. 阶段一的定位

阶段一实现的是一个可以独立验证的“通用记忆底座”，不是完整的主动记忆产品。

它已经支持：

- 显式注册一个场景及其合法记忆类型；
- 由可信用户上下文手动创建一条记忆；
- 保存记忆内容、当前状态、保存理由和来源表达；
- 按当前用户查询详情和列表；
- 默认排除非活动记忆，也可以显式列出非活动记忆；
- 使用 SQLite 持久化，并通过数据库约束保护关键不变量；
- 对跨用户读取、写入和 identifier 猜测进行安全失败；
- 使用中性测试场景验证 Core 没有写死具体业务概念。

它还不支持：

- 从聊天内容自动抽取记忆；
- 自动保存、待确认、丢弃和敏感拦截；
- 同一记忆的第二个 revision；
- 重复、补充、修正、替代和冲突处理；
- 语义召回；
- 将记忆注入 Agent 回答；
- 用户撤销、修正、删除等管理操作。

因此现在运行 `agent-lab chat` 时，聊天流程不会调用 Memory Core。阶段一先把数据
结构、扩展方式和安全边界做稳，阶段二才会增加捕获，阶段三才会增加主动召回和
Agent Runtime 集成。

## 3. 用一个具体例子理解阶段一

假设用户在项目协作场景中明确说：

> 以后项目周报默认用表格。

阶段一不会自动读取这句话，而是由调用方构造一个手动创建命令：

```python
principal = PrincipalContext("analyst-a")

command = CreateMemoryCommand(
    scenario="project-work",
    subject="weekly-report",
    memory_type="preference",
    content="项目周报默认使用表格",
    assertion_kind=AssertionKind.USER_VIEW,
    lifecycle_status=LifecycleStatus.ACTIVE,
    conversation_id="session-1",
    source_turn_id="session-1-turn-1",
    source_expression="以后项目周报默认用表格",
    save_rationale="明确且持续有效的用户偏好",
    observed_at=datetime.now(UTC),
)

record = service.create_memory(principal, command)
```

最终形成的不是一段孤立文本，而是一张可追踪的记忆卡片：

```text
MemoryRecord
├── MemoryItem
│   ├── owner: analyst-a
│   ├── scenario: project-work
│   ├── subject: weekly-report
│   └── type: preference
├── 当前 MemoryRevision
│   ├── content: 项目周报默认使用表格
│   ├── assertion: user_view
│   ├── lifecycle: active
│   └── rationale: 明确且持续有效的用户偏好
└── Evidence
    ├── conversation: session-1
    ├── turn: session-1-turn-1
    └── expression: 以后项目周报默认用表格
```

这样拆分后，后续可以增加新 revision 而不改变逻辑记忆身份，也可以为同一
revision 增加多个来源。阶段一只创建 `revision_number = 1`，但数据结构已经为
后续演进留下边界。

## 4. 总体架构

### 4.1 分层

```text
调用方 / 未来的 Agent Runtime
             │
             │ PrincipalContext + Command
             ▼
┌──────────────────────────────┐
│ application                  │
│ MemoryService                │
│ 用例编排、场景校验、对象创建 │
└──────────────┬───────────────┘
               │ 依赖抽象接口
               ▼
┌──────────────────────────────┐
│ domain + ports               │
│ 领域对象、ScenarioPolicy、   │
│ MemoryRepository             │
└──────────────┬───────────────┘
               ▲
               │ 实现接口
┌──────────────┴───────────────┐
│ adapters                     │
│ SQLiteMemoryRepository       │
│ InMemoryMemoryRepository     │
└──────────────────────────────┘
```

依赖方向是：

```text
application → domain / ports
adapters    → domain / ports
composition → application / adapters / ports
场景插件    → ports
```

Core 不导入具体场景模块。未来的投资假设、调研问题等场景可以实现
`ScenarioPolicy`，但不能反过来修改 Core 的 owner、来源和生命周期规则。

### 4.2 为什么不直接在 Agent 代码里写 SQLite

如果 Agent 直接拼 SQL，会把模型编排、用户身份、记忆规则和数据库实现混在一起，
后续难以测试，也难以迁移存储。当前结构让上层只依赖 `MemoryService`：

- Agent 不需要知道 SQLite 表名；
- 领域模型不需要知道数据库连接；
- 测试可以用内存 Repository 快速验证规则；
- 真实验收可以换成 SQLite Repository；
- 未来迁移其他数据库时，应用用例不需要整体重写。

## 5. 代码目录与阅读顺序

```text
src/agent_lab/memory/
├── domain/
│   └── models.py                  # 领域对象和通用状态
├── ports/
│   ├── scenarios.py               # 场景扩展契约和注册表
│   └── repositories.py            # 持久化接口
├── application/
│   ├── commands.py                # 手动创建输入
│   └── service.py                 # 阶段一应用用例
├── adapters/
│   ├── in_memory.py               # 单元测试替身
│   └── sqlite/
│       ├── repository.py           # 真实 SQLite 读写
│       ├── runtime.py              # 迁移和健康检查命令
│       └── migrations/
│           └── 0001_memory_core.sql
├── composition.py                 # 服务组装和显式场景注册
├── exceptions.py                  # 可预期业务异常
└── __init__.py                    # 对外公开 API
```

建议按以下顺序阅读：

1. `examples/memory_phase_one.py`：先看完整调用；
2. `application/commands.py`：理解输入；
3. `application/service.py`：理解创建和查询流程；
4. `domain/models.py`：理解返回结果；
5. `ports/scenarios.py`：理解场景扩展；
6. `ports/repositories.py`：理解存储边界；
7. `adapters/sqlite/repository.py`：理解真实 SQL；
8. `migrations/0001_memory_core.sql`：理解最终数据库约束；
9. `tests/memory`：看每条设计约束如何被验证。

## 6. 核心对象

### 6.1 PrincipalContext

`PrincipalContext` 表示应用边界已经确认过的当前用户：

```python
PrincipalContext(owner_id="analyst-a")
```

创建命令没有 `owner_id` 字段，这是刻意设计的。owner 不能由模型输出、请求正文
或记忆候选决定，只能来自可信上下文。

当前项目还没有正式身份认证，所以示例中的 `analyst-a` 是虚拟用户。以后接入
HTTP、CLI 或企业身份系统时，应在进入 Memory Core 之前完成认证，再构造
`PrincipalContext`。

### 6.2 CreateMemoryCommand

`CreateMemoryCommand` 是阶段一唯一的写入命令，包含：

| 字段 | 含义 |
| --- | --- |
| `scenario` | 当前记忆属于哪个已注册场景 |
| `subject` | 记忆所描述的稳定对象 |
| `memory_type` | 场景定义的原子类型 |
| `content` | 规范化后的记忆内容 |
| `assertion_kind` | 内容是用户观点、用户提供事实、外部事实还是系统推断 |
| `lifecycle_status` | Core 定义的有效状态 |
| `business_progress` | 可选，由场景定义的业务进展 |
| `conversation_id` | 来源会话 |
| `source_turn_id` | 来源轮次 |
| `source_expression` | 允许持久化的原始表达 |
| `save_rationale` | 为什么值得保存 |
| `observed_at` | 观察到该表达的时刻，必须带时区 |

命令在构造时会拒绝空字符串、非法枚举和不带时区的时间。它负责输入形状校验，
场景是否合法则由 `ScenarioRegistry` 校验。

### 6.3 MemoryItem

`MemoryItem` 是跨 revision 稳定的逻辑身份：

```text
memory_id + owner_id + scenario + subject + memory_type
```

例如“analyst-a 在 project-work 场景中对 weekly-report 的 preference”是一条
逻辑记忆。以后内容发生修正时，理想状态是保留 `memory_id`，新增 revision。

### 6.4 MemoryRevision

`MemoryRevision` 保存某个时点的内容：

- `revision_number`；
- `content`；
- `assertion_kind`；
- `lifecycle_status`；
- `business_progress`；
- `save_rationale`；
- `observed_at` 和 `created_at`；
- `is_current`。

Core 的生命周期只有：

| 状态 | 含义 |
| --- | --- |
| `active` | 当前有效，可进入普通列表和未来的正常召回 |
| `superseded` | 已被后续内容替代 |
| `expired` | 因明确期限失效 |
| `revoked` | 被用户撤销 |

业务进展不能代替生命周期。例如未来投资场景的 `supported` 或 `weakened` 只能放在
`business_progress`，不能重新解释 `active`。

阶段一每条 Item 只有一个初始 revision。虽然接口中有 `include_inactive`，当前
它只是决定是否显示状态为 superseded、expired 或 revoked 的卡片，并不等同于
阶段三将实现的完整多 revision 历史。

### 6.5 Evidence

`Evidence` 回答“这条记忆从哪里来”：

```text
conversation_id + source_turn_id + source_expression
```

每个 Evidence 同时保存 `memory_id`、`revision_id` 和 `owner_id`。阶段一强制每个
MemoryRecord 至少包含一条来源，数据库还要求 revision 指定一条
`primary_evidence_id`。

### 6.6 MemoryRecord

`MemoryRecord` 是应用层一次返回的完整聚合：

```python
MemoryRecord(
    item=...,
    current_revision=...,
    evidence=(...,),
)
```

它在构造时检查 Item、Revision 和 Evidence 的 memory、revision、owner 必须一致，
并拒绝没有来源或把非当前 revision 冒充当前 revision 的记录。

## 7. 场景扩展机制

### 7.1 ScenarioPolicy

`ScenarioPolicy` 是一个 Python `Protocol`。场景通过它声明差异：

```python
@dataclass(frozen=True)
class ProjectWorkPolicy:
    scenario_id = "project-work"
    memory_types = frozenset({"preference", "ongoing_item", "stable_context"})
    business_progress_values = frozenset({"open", "done"})
    allowed_relations = frozenset()
    capture_guidance = "Capture durable project-work context."
    relation_rules = {}
    recall_priorities = {}
```

阶段一真正使用的是：

- `scenario_id`；
- `memory_types`；
- `business_progress_values`。

其余字段是为了让后续捕获、关系和召回仍通过同一个扩展边界接入，目前没有执行
相应逻辑。

### 7.2 ScenarioRegistry

`ScenarioRegistry` 保存当前进程已注册的策略，并负责：

- 拒绝空或未规范化的场景 ID；
- 拒绝没有合法记忆类型的策略；
- 拒绝重复注册；
- 拒绝未注册场景；
- 校验记忆类型；
- 校验业务进展。

没有默认场景，也不会在拼写错误时自动回退。无法识别的场景必须安全失败。

### 7.3 为什么还要登记到数据库

只做进程内校验仍可能被错误脚本或绕过应用服务的代码破坏。因此
`MemoryService.register_scenario()` 会同时：

1. 在不改变进程状态的情况下预校验 policy；
2. 调用 Repository，把场景和合法类型写入数据库目录表；
3. 持久化成功后才注册到 `ScenarioRegistry`。

SQLite 中的复合外键随后保证 `memory_items(scenario, memory_type)` 必须出现在
`memory_scenario_types` 中。重复登记同一 policy 时，SQLite 还会同步移除 policy
已经不再声明的类型；如果已有记忆仍引用该类型，外键会拒绝删除并回滚事务。
这样数据库失败不会留下“进程认为已注册、数据库却未注册”的半完成状态。

## 8. 服务组装

`create_memory_service()` 是当前最小 composition root：

```python
service = create_memory_service(
    SQLiteMemoryRepository(connection_factory(database_path)),
    [ProjectWorkPolicy()],
)
```

它会：

1. 创建一个新的 `ScenarioRegistry`；
2. 创建 `MemoryService`；
3. 逐个显式注册传入的 policy；
4. 同步更新 SQLite 场景目录；
5. 返回可使用的服务。

显式传入 policy 可以让测试清楚地看到当前启用了哪些场景，也避免 Core 在导入时
偷偷注册具体业务模块。

## 9. 创建记忆的完整执行流程

调用：

```python
service.create_memory(principal, command)
```

执行顺序如下：

```text
1. 调用方提供可信 PrincipalContext
          │
2. ScenarioRegistry 校验场景、类型、业务进展
          │
3. MemoryService 生成 memory、revision、evidence UUID
          │
4. MemoryService 读取统一 clock 生成 created_at
          │
5. 构造 MemoryItem
          │
6. 构造 revision_number=1 的 MemoryRevision
          │
7. 构造至少一条 Evidence
          │
8. 组合并再次校验 MemoryRecord
          │
9. Repository 检查 record.owner == principal.owner
          │
10. 一个 SQLite 事务写入 Item、Revision、Evidence
          │
11. 提交时验证全部外键和延迟主来源约束
          │
12. 返回创建后的 MemoryRecord
```

其中 owner 始终取自：

```python
principal.owner_id
```

而不是 `CreateMemoryCommand`。UUID 和 `created_at` 也由服务生成，不信任外部输入。
`observed_at` 表示来源发生时间，因此来自命令，但必须是带时区的时间。

三个数据库写入放在同一个事务中。任何一步失败，Item、Revision 和 Evidence 都不
会留下半套数据。

## 10. 查询的完整执行流程

### 10.1 查询详情

调用：

```python
service.get_memory(principal, memory_id)
```

Repository 的 SQL 同时带上：

```sql
WHERE i.owner_id = ? AND i.memory_id = ?
```

因此只有 ID 正确但 owner 不匹配时，结果仍然为空。应用层统一抛出：

```text
MemoryNotFoundError("memory is unavailable")
```

不存在和越权使用相同错误，不向调用方泄漏“这个 ID 属于其他用户”。

读取 Item 和当前 Revision 后，Evidence 查询再次带：

```sql
WHERE owner_id = ? AND revision_id = ?
```

### 10.2 查询列表

调用：

```python
service.list_memories(principal)
```

默认只返回：

```sql
i.owner_id = 当前用户
AND r.is_current = 1
AND r.lifecycle_status = 'active'
```

显式调用：

```python
service.list_memories(principal, include_inactive=True)
```

会保留 owner 和当前 revision 限定，但不再过滤 lifecycle status。

## 11. Repository 设计

### 11.1 MemoryRepository port

应用层只依赖四个抽象操作：

```text
register_scenario(policy)
add(principal, record)
get(principal, memory_id)
list(principal, active_only)
```

每个用户数据操作都要求显式传入 `PrincipalContext`。这使“忘记限定用户”在接口
层就更难发生。

### 11.2 InMemoryMemoryRepository

内存版本用于：

- 快速单元测试；
- 验证应用规则；
- 不创建数据库的局部开发。

它会模拟 owner、已注册场景、合法类型和活动状态过滤，但不承担真实持久化验收，
也不能代替 SQLite 的外键与事务测试。

### 11.3 SQLiteMemoryRepository

SQLite 版本是当前原型的权威持久化实现。连接工厂为每次操作创建连接，并设置：

```sql
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

- `foreign_keys` 必须在每个 SQLite 连接上显式启用；
- `busy_timeout` 允许短暂写锁竞争等待最多 5 秒；
- `row_factory = sqlite3.Row` 让读取代码按列名恢复领域对象；
- 每次操作结束后关闭连接；
- UUID 存为 TEXT，读出后恢复为 `UUID`；
- 带时区 datetime 存为 ISO 8601 TEXT，读出后使用 `fromisoformat` 恢复；
- bool 存为 0/1。

当前是单机、低并发原型。Repository port 保留了未来迁移其他数据库的可能，但
阶段一不为尚未出现的并发需求引入数据库服务。

## 12. SQLite 数据模型

### 12.1 表关系

```text
memory_scenarios
       │ 1
       ▼ N
memory_scenario_types
       │ 1
       ▼ N
memory_items
       │ 1
       ▼ N
memory_revisions
       │ 1
       ▼ N
memory_evidence
```

另外：

- revision 必须指向同一 owner、同一 memory 的 Item；
- Evidence 必须指向同一 owner、同一 memory 的 Revision；
- Revision 必须指定一条属于自己的 primary Evidence。

### 12.2 memory_scenarios

保存已注册场景：

| 列 | 说明 |
| --- | --- |
| `scenario_id` | 主键，不能为空 |

### 12.3 memory_scenario_types

保存每个场景允许的记忆类型：

| 列 | 说明 |
| --- | --- |
| `scenario_id` | 外键到场景目录 |
| `memory_type` | 当前场景允许的原子类型 |

二者组成主键。

### 12.4 memory_items

保存逻辑记忆身份：

| 列 | 说明 |
| --- | --- |
| `memory_id` | UUID 文本主键 |
| `owner_id` | 所属用户 |
| `scenario` | 场景 |
| `subject` | 稳定对象 |
| `memory_type` | 场景类型 |
| `created_at` | 创建时间 |

主要约束：

- owner、subject 非空；
- `(scenario, memory_type)` 必须已经注册；
- `(memory_id, owner_id)` 唯一，供后续复合外键使用。

### 12.5 memory_revisions

保存不可变内容版本：

| 列 | 说明 |
| --- | --- |
| `revision_id` | revision 主键 |
| `memory_id`、`owner_id` | 归属的逻辑记忆和用户 |
| `revision_number` | 从 1 开始的版本号 |
| `content` | 当前版本正文 |
| `assertion_kind` | 内容性质 |
| `lifecycle_status` | Core 生命周期 |
| `business_progress` | 可选业务进展 |
| `save_rationale` | 保存理由 |
| `observed_at`、`created_at` | 观察和入库时间 |
| `is_current` | 是否为当前 revision |
| `primary_evidence_id` | 主来源 |

主要约束：

- `(memory_id, owner_id)` 必须指向同一用户的 Item；
- revision number 必须大于 0；
- assertion kind 和 lifecycle status 必须属于 Core 枚举；
- `(memory_id, revision_number)` 唯一；
- 部分唯一索引保证同一 memory 最多一个 `is_current = 1`。

### 12.6 memory_evidence

保存来源：

| 列 | 说明 |
| --- | --- |
| `evidence_id` | 来源主键 |
| `memory_id`、`revision_id`、`owner_id` | 来源归属 |
| `conversation_id` | 来源会话 |
| `source_turn_id` | 来源轮次 |
| `source_expression` | 允许保存的表达 |
| `observed_at`、`created_at` | 来源时间和入库时间 |

复合外键要求 Evidence 的 revision、memory 和 owner 同时匹配，不能把用户 B 的来源
挂到用户 A 的 revision。

### 12.7 为什么 primary Evidence 使用延迟外键

Revision 要求至少有一条主来源，但 Evidence 本身又必须引用 Revision，二者形成
环形依赖：

```text
Revision.primary_evidence_id → Evidence
Evidence.revision_id         → Revision
```

迁移把主来源外键定义为：

```sql
DEFERRABLE INITIALLY DEFERRED
```

这样同一事务内可以先插入 Revision，再插入 Evidence，等事务提交时统一验证：

- 主来源确实存在；
- 主来源属于这个 revision；
- memory 和 owner 完全一致。

如果 Evidence 没有写入，事务会在提交时失败，避免产生没有来源的记忆。

## 13. 用户隔离设计

阶段一采用多层防护，而不是只依赖某一个 `if`。

| 层次 | 防护 |
| --- | --- |
| 调用边界 | owner 只能由 `PrincipalContext` 提供 |
| Command | 不允许调用方在记忆正文中指定 owner |
| Application | 创建 Item、Revision、Evidence 时统一使用 principal owner |
| Repository 接口 | 所有读写显式接收 principal |
| Repository 写入 | 再次检查 record owner 与 principal 一致 |
| Repository 查询 | SQL 始终包含 owner 条件 |
| 数据库 | Item、Revision、Evidence 使用 owner 复合约束 |
| 错误信息 | 越权和不存在返回相同结果 |
| 自动化测试 | identifier 猜测、跨用户读写和来源挂接均有负向测试 |

SQLite 没有 PostgreSQL Row-Level Security，因此不能把当前实现描述为生产身份系统。
阶段一证明的是：在单机原型边界内，应用和 Repository 不会因为只知道 identifier
就返回其他用户的数据。

## 14. 迁移与健康检查

### 14.1 默认路径

```text
.agent-lab/memory.db
```

该目录已被 Git 忽略。可以通过环境变量修改：

```dotenv
MEMORY_DATABASE_PATH=.agent-lab/memory.db
```

SQLite 运行入口通过 `MemorySettings` 读取 `.env`。它只要求记忆路径和日志
配置，不会因为缺少聊天模型或 Embedding 凭据而拒绝执行。

### 14.2 版本化迁移

运行：

```powershell
uv run python -m agent_lab.memory.adapters.sqlite.runtime migrate
```

运行时会：

1. 创建数据库父目录；
2. 创建 `memory_schema_migrations`；
3. 按文件名排序查找 `.sql`；
4. 跳过已经记录的版本；
5. 使用 `BEGIN IMMEDIATE` 在一个事务中执行迁移并记录版本；
6. 出错时回滚。

重复运行不会重复建表。

### 14.3 健康检查

运行：

```powershell
uv run python -m agent_lab.memory.adapters.sqlite.runtime health
```

健康检查要求：

- 数据库文件已经存在；
- `PRAGMA quick_check` 返回 `ok`；
- 代码目录中的迁移都出现在 `memory_schema_migrations`。

因此一个空文件不能冒充已经初始化完成的 Memory 数据库。

## 15. 测试如何对应设计

### 15.1 test_memory_service.py

主要验证应用和领域规则：

- 创建后 owner、来源、状态和内容保持完整；
- 跨用户 identifier 与不存在不可区分；
- 所有读取按可信 principal 隔离；
- Repository 拒绝 owner 不一致的记录；
- 默认列表排除非活动记忆；
- 未注册场景、非法类型和非法业务进展失败；
- 非法 policy 失败；
- 没有 Evidence 的 MemoryRecord 无效；
- 空 owner 和无时区时间失败。

### 15.2 test_sqlite_repository.py

主要验证真实数据库行为：

- 迁移首次执行和重复执行；
- `quick_check`；
- 真实持久化和重新打开数据库；
- owner 隔离；
- 默认过滤非活动记忆；
- 未注册场景/类型被数据库拒绝；
- 非法 lifecycle status 被拒绝；
- 没有 primary Evidence 的 revision 被拒绝；
- 一个 memory 不能有两个当前 revision；
- 跨 owner Evidence 被拒绝。

### 15.3 test_dependency_boundaries.py

扫描 Memory Core 的 Python AST，确保：

- Core 不导入正式业务场景；
- Core 不定义投资假设、风险、调研问题等正式场景常量。

这类测试不是验证某个结果值，而是防止未来开发逐步破坏依赖方向。

## 16. 手动演示如何对应代码

运行：

```powershell
uv run python examples/memory_phase_one.py
```

演示会：

1. 在 `.agent-lab/demo-memory` 创建独立 SQLite 文件；
2. 应用迁移并运行健康检查；
3. 注册中性 `DemoScenarioPolicy`；
4. 用 `analyst-a` 创建周报偏好；
5. 用 `analyst-a` 读取并打印；
6. 用 `analyst-b` 猜测同一个 memory id；
7. 确认跨用户读取返回 unavailable；
8. 删除本次演示数据库文件。

预期输出：

```text
Created memory: owner=analyst-a scenario=project-work type=preference status=active evidence=1
Cross-user identifier lookup is safely unavailable.
```

## 17. 执行日志如何观察

阶段一演示已经接入项目统一日志模块。运行：

```powershell
$env:LOG_LEVEL = "DEBUG"
uv run python examples/memory_phase_one.py
```

可以看到场景注册、迁移、健康检查、记忆创建、SQLite 事务提交、同用户查询和
跨用户 unavailable 等事件。默认日志文件为：

```text
.agent-lab/logs/agent-lab.log
```

日志只记录 owner 哈希、memory/revision 技术 ID、状态、数量和耗时，不记录
`content` 或 `source_expression`。详细事件和配置见
[项目执行日志设计与使用说明](../logging.md)。

## 18. 阶段一的重要设计取舍

### 18.1 先手动创建，再自动捕获

阶段一不调用模型，是为了先验证“允许保存什么结构”和“谁能够读取”。如果一开始
同时引入模型抽取，很难判断错误来自模型、准入规则、领域模型还是存储。

### 18.2 先通用测试场景，再正式业务场景

`project-work` 只用于证明扩展契约，不代表最终产品场景。Core 中没有
`investment_hypothesis` 等类型，后续场景必须通过 policy 注册。

### 18.3 SQLite 作为原型权威存储

SQLite 满足当前单机原型的事务和约束要求，并且公司电脑不需要安装数据库服务。
现阶段没有证据需要为多人高并发提前引入 PostgreSQL。

### 18.4 不把 owner 当普通字段

owner 是授权边界，不是模型可以生成的业务内容。它从 principal 进入，随后贯穿
Item、Revision、Evidence 和所有 SQL。

### 18.5 来源从第一阶段就是必需项

来源不是后续展示附加信息。没有来源的记忆无法解释为什么存在，也无法在用户
质疑、修正或删除时追踪，因此领域层和数据库层都从第一阶段强制要求 Evidence。

## 19. 当前限制与后续衔接

### 19.1 阶段二从哪里接入

阶段二将在 `MemoryService.create_memory()` 之前增加：

```text
TurnEnvelope
    → 场景约束
    → 敏感预检
    → 模型结构化候选
    → 程序校验
    → auto_save / pending / discard / blocked
```

只有 `auto_save` 或用户确认后的候选才会转换为现有的 MemoryItem、Revision 和
Evidence。现有 owner 和来源规则不应被模型适配器绕过。

### 19.2 阶段三从哪里扩展

阶段三会扩展 Repository 和数据库迁移，以支持：

- 新 revision；
- typed relation；
- pending、usage、audit 和 suppression；
- 当前/历史查询；
- 撤销、修正和删除；
- owner 限定后的结构化匹配和 Python 精确向量检索；
- Agent 回答前查询和回答后使用记录。

届时 `include_inactive` 会与真正的 revision history 区分成更明确的接口。

### 19.3 现阶段不要做的事情

- 不要让 Agent 直接调用 SQLite；
- 不要把模型输出的 owner 写入数据库；
- 不要在 Core 中新增正式业务类型常量；
- 不要原地覆盖 revision content；
- 不要绕过 `ScenarioRegistry` 注册场景；
- 不要把 `business_progress` 当作 lifecycle status；
- 不要把 InMemory Repository 当成持久化验收结果。

## 20. 阶段一完成标准

当前实现已经满足：

- OpenSpec 阶段一 1.1～1.9 全部完成；
- Memory Core 24 项测试全部通过；
- 项目全量 42 项测试全部通过；
- SQLite 迁移、健康检查、事务和约束通过；
- 手动创建、持久化重开和跨用户隔离通过；
- Ruff 格式与静态检查通过；
- OpenSpec strict validation 通过。

阶段一到此冻结。后续新增自动捕获时，应优先复用本阶段的领域对象、场景注册、
principal 和 Repository 边界；如果需要改变这些基础语义，应先更新 OpenSpec
设计和任务，再开始代码实现。
