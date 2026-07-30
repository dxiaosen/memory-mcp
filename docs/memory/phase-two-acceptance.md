# Memory Core 阶段二验收记录

对应 OpenSpec 变更：`add-general-memory-core`，任务 2.1～2.9。

详细对象、调用链和数据库设计见
[Memory Core 阶段二详细设计与代码导读](phase-two-design.md)。

## 验收范围

- `TurnEnvelope`、结构化 Candidate、四种 AdmissionDecision；
- CaptureResult 的 completed、failed 和 reprocess_required 状态；
- ScenarioPolicy 合法类型、捕获提示和 policy version；
- 框架无关结构化模型适配器及版本元数据；
- 模型调用前脱敏和持久化前二次敏感检查；
- 可信 owner、conversation、source turn 和 observed time 覆盖；
- 自动保存、pending、discard 和 blocked 的同步原子持久化；
- source turn + policy version 幂等；
- owner-scoped pending 查看、确认和拒绝；
- 两个测试策略使用不同合法类型；
- SQLite 迁移、重开和跨用户负向测试。

## 验收命令

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run python examples/memory_phase_two.py
openspec-cn validate add-general-memory-core --strict
```

## 本机结果

- Memory Core：40 项全部通过；
- 项目全量回归：58 项全部通过；
- Ruff 格式和静态检查：通过；
- 阶段二 SQLite 离线演示：通过；
- OpenSpec strict validation：通过；
- 测试和演示均未调用外部模型、Embedding 或数据库服务。

## 关键负向结果

- 模型输出未注册类型：捕获状态为 failed，不写 Candidate、Review 或记忆正文；
- 模型临时中断：状态为 reprocess_required，重试沿用 capture id 且不制造副本；
- 模型伪造 owner/source/time：由 PrincipalContext 和 TurnEnvelope 覆盖；
- 密码、账户秘密、真实持仓和交易指令：进入模型前脱敏；
- 模型输出敏感正文：持久化前二次拦截；
- 手动创建敏感正文：使用同一守卫拦截，不写入记忆；
- 同一 source turn 重复执行及 SQLite 重开：不重复调用抽取器，不重复 Memory、
  Review 或 Evidence；
- pending identifier 跨用户访问：与不存在使用相同 unavailable 结果；
- pending 确认：活动记忆创建和 Review 状态变化在同一事务提交；
- pending 拒绝：不创建活动记忆。

## 环境说明

由于公司 Windows 环境不允许 pytest 创建默认缓存目录，项目测试配置禁用了
pytest cacheprovider。该缓存只服务 `--lf`、`--ff` 等测试便利功能，不影响测试
执行、隔离或断言。

阶段二仍使用 Python 标准库 SQLite。结构化模型端口由 Fake 和固定离线 backend
验收；接入真实模型应在 Core 外实现 backend，并继续遵守脱敏输入和结构化输出
契约。

## 边界说明

阶段二的敏感策略证明的是 Memory Capture 的模型输入和长期持久化边界，不代表
生产级数据防泄漏，也不能证明内容在进入 Memory Capture 前没有经过其他系统。

阶段二不包含重复/补充/替代关系、revision 演进、主动召回、删除抑制或 Agent
Runtime 接入；这些属于 OpenSpec 第三阶段。
