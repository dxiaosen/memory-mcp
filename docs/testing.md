# Memory MCP 测试与验收

本文区分“纯自动化替身”“真实 MCP/Core”“真实 PostgreSQL”“真实模型”，避免把
局部 mock 结果描述成端到端。配置字段和测试专用值见
[配置参考](config.md)。

## 1. 测试分层

| 层级 | Repository | Extractor/Model | Transport | 外部服务 |
| --- | --- | --- | --- | --- |
| Core 单元/契约 | InMemory | Fake | 无 | 无 |
| extraction 单元 | 无 | fixed 或 `_StructuredModel` | 无 | 无 |
| MCP transport | InMemory | Fake | 真实 FastMCP/HTTP | 无 |
| Hook 单元 | Fake Client | 不涉及 | Client protocol | 无 |
| PostgreSQL contract | 真实 PostgreSQL | Fake | 部分无 | 专用测试库 |
| PostgreSQL transport E2E | 真实 PostgreSQL | fixed | 真实 HTTP + MCP Client | 专用测试库 |
| Agent Host adapter | InMemory/真实 MCP | Fake | 通用/Codex/Claude Hook JSON + 真实 HTTP | 无 |
| Agent distribution | 无 | 无 | Python 3.11 隔离 wheel/console smoke | 无 |
| real-model E2E | 真实 PostgreSQL | 真实 provider | 真实服务 + 独立 Agent 配置 | 测试库 + 模型 API |

重要边界：

- InMemory 与 Fake 只验证业务和协议，不验证 migration、SQL、连接池和真实模型；
- `fixed` 只由测试代码注入并替换候选生成，MCP、鉴权、Core、PostgreSQL 和 Hook
  都保持真实；
- `examples/hook_runner.py` 的 `_agent` 只是接线 callable，不是业务
  Agent 大模型；真实模型在服务端负责记忆候选抽取；
- real-model E2E 才验证 provider 请求和严格 schema，但模型措辞具有概率性。

## 2. 日常本地检查

```bash
uv sync --all-packages --frozen
.venv/bin/python -m pytest -q
uv run ruff format --check .
uv run ruff check .
git diff --check
openspec-cn validate add-general-memory-core --strict
openspec-cn validate add-agent-active-memory --strict
openspec-cn validate enhance-memory-metadata --strict
openspec-cn validate add-investment-research-profile --strict
```

未显式提供 `MEMORY_MCP_TEST_DATABASE_URL` 时，6 个 PostgreSQL 外部用例应 skip，
而不是读取 `.env` 后静默清库。当前提交的精确测试数以本页最后一次验收记录为准，
不要把 skip 描述成已验证数据库。

```text
117 passed, 6 skipped
```

## 3. PostgreSQL 安全前置检查

外部测试会迁移数据库并执行
`TRUNCATE TABLE memory_profiles CASCADE`。必须满足：

1. database 名称明确包含 `test`；
2. 是允许清空的专用测试库；
3. 账号拥有 migration、DDL/DML 和 truncate 权限；
4. 网络和 SSL 参数与实例能力一致；
5. 不得指向开发共享库或生产库。

先验证 migration 与健康：

```bash
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
```

若刚修改过 console script、移动模块或切换分支，先执行
`uv sync --all-packages --frozen`，否则 `.venv/bin/memory-mcp*` 可能仍引用旧
安装产物。该命令明确同步 Server 与 Agent 两个 workspace member。

测试代码只识别 `MEMORY_MCP_TEST_DATABASE_URL`。如果 `.env` 的
`MEMORY_MCP_DATABASE_URL` 本身就是专用测试库，可用 Python 在同一进程安全映射，
不打印 DSN：

```bash
.venv/bin/python -c '
import os
import pytest
from dotenv import load_dotenv

load_dotenv()
os.environ["MEMORY_MCP_TEST_DATABASE_URL"] = os.environ["MEMORY_MCP_DATABASE_URL"]
raise SystemExit(pytest.main([
    "tests/core/test_postgresql_contract.py",
    "tests/server/test_postgresql_transport.py",
    "-q",
]))
'
```

2026-08-01 已在当前 RDS 配置上成功应用元数据与回滚兼容 migration，随后
`memory-mcp-db health` 通过。该数据库名称不满足测试库的 `test` 防误删条件，因此
本轮没有在它上面运行会 truncate 的 6 个 PostgreSQL pytest；需要完整 SQL 写入回归
时，应另建名称包含 `test` 的可清空数据库再运行本节命令。

## 4. 自动化覆盖清单

### 4.1 Core 与 PostgreSQL

- owner-first 查询和跨 owner identifier 防护；
- capture event 相同 payload replay、不同 payload conflict；
- auto-save / pending / discard / blocked 四种准入；
- 敏感内容在模型和持久化边界前拦截；
- duplicate Evidence 附加到同一 item；
- replacement revision、current 唯一性、history 和 inactive 状态；
- pending confirm/reject 的 owner 隔离和原子状态变化；
- reprocess-required 后使用相同 event 继续处理；
- schema migration 顺序、checksum、必需表和约束健康检查，包括保留历史 checksum
  的 `0003_profile_naming.sql`、新增元数据约束的 `0004_memory_metadata.sql`，以及
  保证旧版短期回滚写入的 `0005_metadata_rollback_compat.sql`；
- revision confidence、verification、sensitivity、validity 的领域不变量和 SQL 映射；
- Evidence document/web/tool citation 字段的消息归属、敏感阻断和持久化往返；
- 到期 revision 在 owner-scoped Repository 查询阶段被排除，但详情/history 保留；
- `revoke_memory` 的 owner 隔离、幂等、立即停止召回和 Evidence 保留；
- `general-work` 与 `investment-research` 的完整 Profile 注册和 Core 依赖边界；
- 投研 thesis/evidence 共存、同原子论点冲突 pending、期限策略、交易内容阻断与非法
  progress 安全失败；
- 进程重启后记忆仍可召回。

### 4.2 MCP transport

- 未认证请求为 401；
- Token 只在服务端映射为 Principal；
- 八个工具的输入 schema 禁止额外字段和 owner 参数；
- read/write/review scope 分离；
- DTO `contract_version=1`、稳定 fingerprint 和字符上限；
- 服务错误转换为稳定、无敏感正文的错误码；
- `/health` 验证 PostgreSQL，异常时返回 503；
- 同 owner Agent A/B 共享、不同 owner 隔离。

### 4.3 Hook 生命周期

- 同一 run key 并发或重复 BeforeRun 只访问服务一次；
- 同一 run key 重复 AfterRun 只提交一次；
- 相同 run key 携带不同 payload 抛 typed conflict；
- 完成 receipt cache 有界，且不会取消执行中的任务；
- 空召回得到 `memory_context=None`；
- Agent callable 抛错或取消时不执行 AfterRun；
- AfterRun 有界重试复用同一 event id 和 payload；
- capture summary、failure code、replay 标记完整透传；
- fail-open 返回稳定 warning，fail-closed 抛 typed error；
- HTTP 连接池跨工具调用复用并显式关闭；
- Ctrl+C 关闭服务资源且进程不输出 KeyboardInterrupt traceback。
- 标准 `run_id`、Codex `turn_id` 与 Claude Code `prompt_id` 归一化为稳定顶层 turn；
- `UserPromptSubmit` 输出严格 `additionalContext` JSON，`Stop` 输出空成功 JSON；
- 两个独立 Hook 进程通过权限受限的原子状态文件关联；
- 并发轮次不串 prompt，缺失/过期/损坏状态安全跳过；
- `SubagentStop` 不产生独立 capture；
- 第三方标准 BeforeRun/AfterRun 不修改 Bridge、状态或 Core 即可完成闭环；
- 真实 HTTP/MCP 中 Codex 写入后 Claude/通用 Agent 可跨 Agent 召回，不同 owner 隔离。

### 4.4 生产配置边界

- 服务端模板不包含 `MEMORY_HOOK_*`、fixed candidate 或测试数据库；
- Agent 模板只包含 `MEMORY_MCP_URL` 和 `MEMORY_MCP_TOKEN`；
- 真实抽取缺少 model/API key 时拒绝构造；
- 运行配置不接受 fixed backend 或候选 JSON；
- 静态 Token 少于 32 字符时拒绝服务启动；
- Hook 首选两个 `MEMORY_MCP_*` 连接变量并兼容旧名称，不从根目录 `.env`
  隐式加载其他 Agent 凭据；
- 内置 Codex/Claude Code 配置模板只注册顶层事件且不包含地址或 Token。

### 4.5 发行包隔离

自动化依赖守卫会扫描两个源码树：

- `server/src/memory_mcp` 不得导入 `memory_mcp_agent`；
- `agent/src/memory_mcp_agent` 的第三方 import 只允许 `httpx`、
  `pydantic` 和 `pydantic_settings`；
- Server console scripts 只有 `memory-mcp`、`memory-mcp-db`；
- Agent console script 只有 `memory-mcp-hook`；
- Server production dependencies 不包含 Agent distribution。

发布前还要构建真实 wheel 并在空 Python 3.11 环境安装：

```bash
uv build --package memory-mcp-agent --wheel
uv venv /tmp/memory-mcp-agent-wheel --python 3.11
uv pip install \
  --python /tmp/memory-mcp-agent-wheel/bin/python \
  dist/memory_mcp_agent-0.1.0-py3-none-any.whl
```

隔离环境必须满足：

- `import memory_mcp_agent` 成功；
- `memory-mcp-hook` 可执行；
- `memory-mcp` 和 `memory-mcp-db` 不存在；
- `memory_mcp`、`mcp`、`psycopg`、`langchain_core` 均不可导入；
- 安装闭包只有 Agent wheel 及 HTTP/Pydantic 的传递依赖。

真实 HTTP transport 用例再从该逻辑 Client 调用 Server，验证 initialize、认证、
`recall_memory`、`capture_completed_turn`、Codex/Claude 字段归一化和 owner 隔离。
这两类证据共同避免“wheel 看起来轻量，但运行仍偷用仓库 Server”的假隔离。

## 5. fixed 注入的自动化 PostgreSQL E2E

此路径验证身份、数据库和完整远程链路，不消耗模型额度。测试函数在代码中构造
候选 fixture 和 `FixedCandidateBackend`，通过 `candidate_extractor` 注入服务，
不读取 backend 或 fixed candidate 环境变量。

它验证 Agent A 输入固定证据后创建记忆、同 owner Agent B 召回，以及不同 owner
同查询返回 0：

```text
Hook -> HTTP/MCP -> auth -> Core -> injected fixed candidate -> PostgreSQL
     -> receipt -> 另一 Agent 的 recall
```

运行命令见[端到端使用](usage.md)。这里的 fixed 是测试 adapter，不是可部署的
服务模式。

## 6. 真实模型 E2E

### 6.1 前置条件

- `MEMORY_MCP_MODEL_NAME` 和 `MEMORY_MCP_MODEL_API_KEY` 已配置；
- provider/model/API key/base URL 可用；
- 真实模型支持当前 Chat Completions/结构化输出协议；
- 仍然使用可清理的测试数据库；
- 输入为明确、长期、可在原文中定位的陈述。

DeepSeek provider 会在 adapter 中关闭 V4 默认 thinking，避免 thinking mode 拒绝
named `tool_choice`。该行为有单元测试，且本轮已用真实 API 验证
`CandidateBatch` 返回。

### 6.2 本轮实际流程

2026-07-31 使用 `.env` 的真实模型和 PostgreSQL 执行：

1. Agent A BeforeRun 召回 0；
2. Agent A AfterRun 抽取一条长期项目文档决策并 auto-save；
3. 同 owner Agent B 省略 subject，以项目名/文档关键词召回 1；
4. Agent B 的上下文含安全 header、revision/type/subject/assertion/time 和正文；
5. 不同 owner 的 Agent B 用同样 query 召回 0；
6. 查询轮次 AfterRun 返回 completed、0 新候选；
7. 模型 HTTP 200，服务 capture completed；
8. 服务 Ctrl+C 关闭 PostgreSQL/MCP 生命周期，退出码 0。

端到端日志样本耗时：

- owner-first recall 约 0.2–0.35 秒；
- 真实模型 capture 约 1.2–2.5 秒。

这些只是本轮 smoke 样本，不是容量承诺或 P95/SLA。阶段六仍需在部署网络上做并发、
超时和恢复压测。

### 6.3 断言方式

真实模型测试应断言：

- 返回值能解析为 `CandidateBatch`；
- 每个 `source_expression` 是输入的连续子串；
- memory type 属于当前记忆配置允许集合；
- capture 为 completed/reprocess-required 等已知状态；
- auto-save/pending/discard/blocked 符合保守准入边界；
- owner 共享和隔离结果正确；
- 默认日志不含正文；内容模式可见通过敏感检查的正文；
- 两种模式都不含 Secret 和敏感规则拦截的原文。

不要把模型选择的 `subject`、`memory_type` 或具体改写文本作为永远不变的金标。

## 7. `subject` 测试注意事项

`subject` 在 Repository 查询前做精确过滤，不参与模糊打分。真实模型可能把
“documentation-style” 归纳为“Atlas-731项目”，因此写入时给模型的 subject hint
并不保证成为最终 subject。

- fixed 测试：候选 subject 已知，可在 recall 传完全一致的值；
- 真实模型测试：通用检索先省略 subject，通过 query/task intent 验证召回；
- 业务 Host：只有具备规范 subject 枚举或服务端统一归一化时才使用过滤；
- 召回为 0 时，应先去掉 subject 再判断是相关性问题还是隔离/保存问题。

## 8. 故障与安全用例

| 场景 | 预期 |
| --- | --- |
| 数据库不可达 | 启动/health 失败，Agent 按 Hook fail-open 策略继续或报错 |
| 模型 400/超时 | capture 标记 reprocess-required，不保存半成品 |
| 模型输出缺字段/额外字段 | `invalid_candidate_output` |
| source expression 不在原文 | 候选被拒绝 |
| 含密码/API key 等敏感值 | 模型前脱敏或 blocked，不记录原文 |
| 相同 event、相同 payload | replay，不重复保存 |
| 相同 event、不同 payload | `idempotency_conflict` |
| Token 未映射/Scope 不足 | 401 或稳定 forbidden 错误 |
| 不同 owner 猜测 memory id | not found，不泄露存在性 |
| 到期或 revoked memory | 普通 list/recall 不返回，详情/history 仍可审计 |
| citation 带凭据文本 | 候选 blocked，禁止值不落库、不响应、不记录日志 |
| 高 confidence 的外部证据 | 保留 confidence，但不能自动标为 `source_verified` |
| 投研候选使用非法 progress | capture failed，无部分写入 |
| 记忆服务短暂失败 | 默认 Hook warning，Agent 主任务继续 |

## 9. 部署后验收

本地测试不替代以下阶段六工作：

- 公网 HTTPS、证书和负载均衡健康检查；
- ECS → RDS 私网和 Agent Host → MCP 的真实网络路径；
- 安全组、日志目录权限、systemd restart/rollback；
- 多进程并发、模型限流、数据库连接池和恢复压测；
- 10–15 条现场脚本、录屏和最终交付验收。

部署步骤见[部署指南](deploy.md)。
