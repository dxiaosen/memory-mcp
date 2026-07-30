# Memory Core 阶段二详细设计与代码导读

## 1. 阶段二解决什么问题

阶段一已经证明一张记忆卡片如何保存、属于谁，以及 SQLite 如何保证基本约束。
阶段二在这个基础上增加“从一轮会话形成候选”的流程：

```text
完成的一轮会话
    → 敏感预检和脱敏
    → 场景提供合法类型与抽取说明
    → 结构化候选抽取
    → 程序校验可信身份和来源
    → 自动保存 / 待确认 / 丢弃 / 敏感拦截
```

这一阶段仍未接入现有 Agent Runtime，也不判断重复、补充、冲突或替代，不做
语义召回。阶段二的重点是先保证“发现和准入”正确、安全、幂等。

## 2. 用一个具体例子理解

输入一轮虚拟会话：

> 以后项目周报默认用表格。接口重构下周还要继续跟进。这次回答短一点。
> 我可能偏好蓝色。密码是 fictional-demo-secret。

期望拆成五个独立结果：

| 内容 | 程序决策 | 后续位置 |
| --- | --- | --- |
| 周报默认用表格 | 自动保存 | 活动记忆 |
| 接口重构下周继续 | 自动保存 | 活动记忆 |
| 这次回答短一点 | 丢弃 | 只保留无正文结果 |
| 可能偏好蓝色 | 待确认 | ReviewItem |
| 密码 | 敏感拦截 | 只保留拦截类别 |

这里有三个关键点：

1. 模型只建议候选，不决定 owner、source turn 和观察时间；
2. pending 在确认前不属于活动记忆；
3. 密码原文不会进入模型请求、候选表、记忆表、Evidence、日志或 CaptureResult。

离线运行：

```powershell
uv run python examples/memory_phase_two.py
```

示例使用固定结构化后端，不调用外部模型，目的是演示 Core 的流程和边界，不代表
模型抽取效果。

## 3. 新增领域对象

实现位置：

```text
src/agent_lab/memory/domain/capture.py
```

### 3.1 TurnEnvelope

`TurnEnvelope` 表示已经完成、可以进入捕获流程的一轮会话：

| 字段 | 来源 | 是否可信 |
| --- | --- | --- |
| `scenario` | 调用方 | 由注册表校验 |
| `conversation_id` | Runtime | 是 |
| `source_turn_id` | Runtime | 是 |
| `content` | 用户会话 | 不可信正文 |
| `observed_at` | Runtime | 是 |

owner 不放在 `TurnEnvelope` 中，而是通过独立的 `PrincipalContext` 传入，避免把
模型或普通请求字段误当成授权身份。

### 3.2 CandidateProposal 与 Candidate

`CandidateProposal` 是模型适配器给出的未受信建议。它包含：

- subject、memory type 和原子 content；
- assertion kind；
- source expression；
- 保存理由和置信度；
- durable、uncertain 或 temporary；
- explicit、inferred 或 ambiguous；
- 可选业务进展和时间表达；
- 模型可能输出的 owner、conversation、turn 和 observed time。

`MemoryService` 随后创建可信 `Candidate`。即使模型输出了另一用户和另一
source id，也会被当前 `PrincipalContext` 和 `TurnEnvelope` 覆盖。

另外，`source_expression` 必须真实出现在脱敏后的 turn 中，否则整次捕获以
`invalid_candidate_output` 安全失败。

### 3.3 AdmissionDecision

四种结果是互斥枚举：

```text
auto_save
pending
discard
blocked
```

`CaptureOutcome` 只保存 candidate id、决策、原因码以及可选 memory/review id，
不包含候选正文。

### 3.4 ReviewItem

`ReviewItem` 保存一项 pending 候选，状态为：

```text
pending → confirmed
        → rejected
```

确认或拒绝时记录 `decided_at`。确认会在同一个 Repository 事务中创建活动记忆
并修改 Review 状态；拒绝不会创建记忆。

### 3.5 CaptureResult

`CaptureResult` 是 source turn 的幂等回执，状态为：

| 状态 | 含义 | 同 key 再次调用 |
| --- | --- | --- |
| `completed` | 四类结果已原子提交 | 直接返回原结果 |
| `failed` | 结构或策略错误，不能盲目重试 | 直接返回失败回执 |
| `reprocess_required` | 模型等临时处理中断 | 再处理一次 |

回执还记录：

- model id；
- prompt version；
- candidate schema version；
- ScenarioPolicy version。

## 4. 场景如何参与捕获

`ScenarioPolicy` 新增稳定的 `policy_version`。捕获时 Core 只从当前策略读取：

```text
memory_types
capture_guidance
policy_version
business_progress_values
```

这些值组成 `ExtractionRequest`。模型适配器看不到 Core 中不存在的正式业务类型，
Core 也不写死 project-work、投资或调研语义。

测试同时注册：

- `TestScenarioPolicy`：preference、ongoing_item、stable_context；
- `AlternateScenarioPolicy`：note、commitment。

同一个捕获服务可以处理两组类型，证明扩展场景不需要修改 Core。

## 5. 结构化模型适配器

实现位置：

```text
src/agent_lab/memory/adapters/structured_model.py
```

`StructuredCandidateExtractor` 接受一个框架无关的结构化后端：

```python
backend(request) -> Sequence[Mapping[str, object]]
```

适配器负责：

- 检查必填字段；
- 把 assertion、durability 和 expression basis 转为枚举；
- 检查 confidence 范围；
- 解析带时区的时间；
- 暴露 model、prompt 和 schema 版本。

真实模型以后可以在 Memory Core 外实现这个 backend。阶段二测试使用 Fake 和固定
结构化后端，不发起网络请求。

## 6. 敏感预检和脱敏边界

实现位置：

```text
src/agent_lab/memory/adapters/sensitive.py
```

默认 `RegexSensitiveContentGuard` 支持可替换的规则集合，当前演示检测：

- credential 和密码；
- 账户秘密；
- 真实持仓或仓位；
- 交易指令。

处理分两层：

```text
原始 turn
    → 先脱敏，再交给候选抽取器

模型候选 content + source expression
    → 持久化前再次检查

手动 create_memory 的全部可持久化文本
    → 写入前使用同一敏感守卫
```

命中后只保存 `sensitive_<category>` 或 `sensitive_model_output` 原因码，不保存
原文或脱敏前的文本。

这个边界只证明 Memory Capture 不会长期保存禁止正文。它不等于生产级 DLP，也
不能证明同一段内容在进入 Memory Capture 之前没有被其他 Agent 或模型处理。
演示和测试只能使用虚构或公开净化后的内容。

## 7. 确定性准入规则

实现位置：

```text
src/agent_lab/memory/application/admission.py
```

第一版规则按以下顺序执行：

| 条件 | 决策 |
| --- | --- |
| `temporary` | discard |
| 持久性不确定 | pending |
| system inference | pending |
| inferred 或 ambiguous | pending |
| confidence 低于 0.8 | pending |
| 其余明确、持久、高置信候选 | auto_save |

敏感 blocked 在准入规则之前处理。模型不能直接返回最终决策，这保证同一结构化
候选在相同程序配置下得到稳定结果。

## 8. SQLite 数据模型

迁移文件：

```text
0002_memory_capture.sql
```

### 8.1 memory_capture_runs

保存一次 source turn 捕获的无正文回执和版本元数据。幂等键为：

```text
owner_id
+ scenario
+ conversation_id
+ source_turn_id
+ policy_version
```

因此同一用户、同一场景、同一 source turn 和同一策略版本最多只有一次最终结果。

### 8.2 memory_capture_outcomes

只保存决策和技术引用：

```text
auto_save → memory_id
pending   → review_id
discard   → 无正文引用
blocked   → 无正文引用
```

数据库 CHECK 约束保证四种引用形状不能混用。

### 8.3 memory_review_items

保存允许用户查看的 pending 候选。表中包含 owner，并通过复合外键关联所属 capture。
所有 list、get、confirm 和 reject SQL 都显式带 `owner_id`。

### 8.4 memory_revisions 的时间字段

阶段二增加：

- `original_time_expression`：例如“下周”；
- `normalized_time`：例如带时区的具体日期。

原始表达、source turn 的 `observed_at` 和可选规范化时间彼此独立，避免把模型的
时间解释伪装成用户原话。

## 9. 原子事务和幂等

`MemoryRepository.commit_capture()` 一次提交：

```text
capture run
+ 自动保存的 MemoryItem / Revision / Evidence
+ pending ReviewItem
+ 无正文 CaptureOutcome
```

任一步失败，SQLite 会回滚全部内容。临时模型失败只提交
`reprocess_required` 回执，不提交 Candidate、Review、Evidence 或活动记忆。

重试时沿用原 capture id；完成后再次调用直接读取持久化结果，不再调用模型。

## 10. pending 用户操作

应用接口：

```text
list_pending_reviews(principal)
get_review(principal, review_id)
confirm_review(principal, review_id)
reject_review(principal, review_id)
```

跨用户 identifier 与不存在使用相同的 `review is unavailable` 错误，不暴露 owner
或候选正文。确认前，普通 `list_memories()` 不会返回 pending 内容。

## 11. 代码阅读顺序

建议按以下顺序阅读：

1. `domain/capture.py`：对象和状态；
2. `ports/capture.py`：模型与敏感边界；
3. `application/admission.py`：确定性准入；
4. `application/capture_service.py`：完整捕获调用链；
5. `application/service.py`：对外应用门面；
6. `ports/repositories.py`：原子持久化契约；
7. `adapters/sqlite/migrations/0002_memory_capture.sql`：数据库约束；
8. `adapters/sqlite/repository.py`：SQLite 实现；
9. `tests/memory/test_capture_service.py`：业务验收；
10. `examples/memory_phase_two.py`：离线演示。

## 12. 测试如何对应需求

`test_capture_service.py` 覆盖：

- 一轮多个原子候选；
- 四种互斥准入结果；
- 用户观点和弱推断；
- 临时指令丢弃；
- 相对时间原文和规范化时间；
- 模型 owner/source/time 被可信值覆盖；
- 敏感正文进入模型前脱敏、模型输出落库前二次拦截；
- 手动创建不能绕过敏感持久化边界；
- source turn 幂等和 SQLite 重开；
- 临时失败重处理；
- pending 的跨用户查看、确认和拒绝；
- 两个测试策略使用不同合法类型。

`test_capture_adapters.py` 覆盖结构化字段解析、非法模型输出和四类敏感模式。

## 13. 手动验证

```powershell
uv run python examples/memory_phase_two.py
uv run pytest tests/memory
uv run ruff check src tests examples
openspec-cn validate add-general-memory-core --strict
```

示例预期显示：

```text
auto_save: 2
pending: 1
discard: 1
blocked: 1
Idempotent replay: True
Other user's pending reviews: 0
```

## 14. 当前限制和阶段三衔接

阶段二有意不做：

- 重复和补充识别；
- 新旧记忆冲突与替代；
- revision 演进；
- 语义检索和主动召回；
- Agent Runtime 接入；
- 完整 AuditEvent 和删除抑制。

阶段三将复用现有 Candidate、CaptureResult、ReviewItem、owner 边界和 SQLite
事务，增加关系、历史、召回、修正、撤销、删除与 Agent 适配。
