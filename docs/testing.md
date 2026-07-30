# Memory MCP 测试说明

## 1. 自动化分层

| 层级 | 覆盖内容 | 是否需要外部服务 |
| --- | --- | --- |
| Core 单元/契约 | owner 隔离、准入、幂等、生命周期、召回 | 否 |
| 模型 adapter | 固定精确匹配、严格 schema、提示词边界 | 否 |
| Hook 单元 | 一次性时机、空召回、失败、重试、稳定 event | 否 |
| MCP transport | 鉴权、DTO、错误码、七个真实 MCP 工具 | 否 |
| PostgreSQL E2E | 真实 HTTP + Hook Runner + PostgreSQL + 多身份 | 专用测试库 |
| 模型 smoke | 真实模型结构化抽取 | 模型 API |

日常全量检查：

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
openspec-cn validate add-general-memory-core --strict
```

## 2. PostgreSQL 自动化 E2E

测试库必须是可清空的专用数据库，且数据库名包含 `test`。测试会迁移并执行
`TRUNCATE TABLE memory_scenarios CASCADE`，严禁指向开发或生产库。

```bash
MEMORY_MCP_TEST_DATABASE_URL='postgresql://.../memory_mcp_test' \
  uv run pytest \
    tests/core/test_postgresql_contract.py \
    tests/server/test_postgresql_transport.py
```

`test_postgresql_hook_runner_cross_agent_end_to_end` 使用真实 Streamable HTTP、
真实 MCP Client、固定 extractor 和真实 PostgreSQL 验证：

1. 用户 A / Agent A 的 AfterRun 保存偏好；
2. 用户 A / Agent B 的 BeforeRun 能召回；
3. 用户 B / Agent B 使用相同查询时结果为空；
4. 每一轮成功后均可安全调用 capture，未命中固定证据时得到零候选而非错误。

## 3. Hook 时机验收

自动化测试明确检查：

- 同一 run key 并发或重复调用 BeforeRun，只访问服务一次；
- 同一 run key 重复调用 AfterRun，只提交一次；
- 同一 run key 携带不同 payload 时返回 typed conflict；
- 已完成 run receipt cache 保持配置上限，且不取消进行中的工作；
- 空召回返回 `memory_context=None`；
- Agent callable 抛错时不执行 AfterRun；
- 捕获重试始终使用同一 event id；
- AfterRun 透传完整 capture summary/failure code；
- MCP Client 复用并显式关闭 HTTP 连接池；
- fail-open 返回稳定 warning，fail-closed 抛出 typed error；
- 两个环境 profile 的 URL 和 Token 独立，Secret 不进入 repr。

如果 Host 有原生 Hook，把 BeforeRun 绑定到顶层任务入口，把
`after_run_success` 绑定到 final response 成功事件。不要绑定到单次 LLM、
tool call、子 Agent 或流式 token 完成事件。

## 4. 真实模型手工 smoke

真实模型不进入确定性 CI。手工验证时：

1. 设置 `MEMORY_MCP_EXTRACTOR_BACKEND=openai-compatible`；
2. 配置有效的 `CHAT_MODEL_PROVIDER`、`CHAT_MODEL_NAME`、
   `CHAT_MODEL_API_KEY`，兼容服务再配置 `CHAT_MODEL_BASE_URL`；
3. 启动服务并用 Agent A 输入一句明确、长期且可原文定位的偏好；
4. 确认 capture receipt 为 `completed`，并有 auto-save 或符合保守策略的
   pending；
5. 用同 owner 的 Agent B 查询并确认召回；
6. 输入临时、含糊或敏感内容，确认分别进入 discard/pending/blocked，而不是
   被无条件保存；
7. 检查日志中没有输入、输出、Token、DSN 和 API Key。

模型结果具有概率性；业务断言只检查 schema、安全边界和准入类型，不把具体措辞
作为稳定自动化预期。固定 backend 是回归测试和现场兜底路径。

## 5. 后续部署验收

公网 HTTPS、ECS 到 RDS 私网连通、证书、负载均衡健康检查、真实远端 Agent Host
兼容性和延迟恢复压测不属于本地阶段五自动化。部署后按
[阿里云 ECS 指南](deployment/aliyun-ecs.md)单独执行，不能用本地回环测试替代。

## 6. 阶段五收尾记录

2026-07-30 已完成：

- 全量本地套件：`78 passed, 6 skipped`；skip 均为未提供外部测试库时的
  PostgreSQL 可选用例；
- 专用真实 RDS 套件：`8 passed`，包含 Repository contract、MCP 重启和新增的
  Hook 跨 Agent/跨用户闭环；结构拆分后已再次通过；
- Ruff format/check、`git diff --check` 通过；
- `openspec-cn validate add-general-memory-core --strict` 通过；
- 阶段五代码与结构收尾全部完成；剩余任务均为阶段六部署与交付验收。
