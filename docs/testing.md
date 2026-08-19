# Memory MCP 测试与验收

质量基准案例与结果见[投研记忆评测](evaluation.md)，运行配置见[配置参考](config.md)。

## 1. 测试分层与目录

测试按层级组织，目录即层级。`conftest.py` 按目录自动标记 marker，
可用 `pytest -m unit/contract/integration/evaluation` 分组执行。

| 层级 | 验证范围 | 外部依赖 | 路径 |
| --- | --- | --- | --- |
| unit | 纯领域规则、纯函数、指标和校验（召回打分、token 估算、分词、profile 契约） | 无 | `tests/unit/` |
| contract | Port 协议契约（Repository、Extractor、Profile、Guard、dependency boundary） | 无（PG 契约除外） | `tests/contract/` |
| integration | 跨层集成（MCP transport、认证、事务、捕获/召回/生命周期全链路、Agent Hook） | 无（PG 集成除外） | `tests/integration/` |
| end-to-end | Agent Hook 到 MCP Server 关键主链路 | InMemory Repository | `tests/end_to_end/` |
| evaluation | 投研候选、关系、召回和安全指标 | 默认无；live 用模型 API | `tests/evaluation/` |

补充约束：

| 项 | 说明 |
| --- | --- |
| `evals/cases.json` | 52 个中文投研质量案例（非 pytest 用例） |
| 测试替身 | 只放在 `tests/support/`（fakes/builders），通过端口注入，不属于 Server 生产包 |
| `tests/support/builders.py` | 高频领域对象构造器（turn/service/capture/record），无副作用、不复制算法 |
| `tests/support/fakes.py` | Fake/Mock 实现（FakeCandidateExtractor、SequentialCandidateExtractor、FakeEmbeddingProvider 等） |
| PostgreSQL 契约 | 只替代候选生成；MCP、认证、Core、数据库和 Hook 仍走真实链路 |
| 生产代码 | 不导入 `tests/`；组合根不用 Fake |

高风险边界必须保留回归覆盖：可信 Principal 派生、Token 默认 Profile、跨 owner 不可见、
scope（`memory:write` runtime-only / `memory:review` 管理；ListTools 过滤 + CallTool 硬授权）、
跨 Profile 版本幂等与冲突、事务回滚、pending review、记忆/关系撤销、PostgreSQL
migration 与重启、MCP 错误合同、Hook 顶层轮次生命周期、候选/关系 schema、
向量降级、关系感知召回、团队提取幂等、revoke stale 关系级联、explicit_uncertainty→Pending、
explicit replacement fallback、Relation best-effort（不回滚 Candidate）、candidate-level discard、
Relation semantic dedupe（manual+automatic 语义等价边去重）。

## 2. 日常检查

```bash
uv sync --all-packages --frozen
uv run ruff check .
uv run pytest -q                    # 全量
uv run pytest -m unit -q           # 仅 unit 层
uv run pytest -m contract -q       # 仅 contract 层
uv run pytest -m integration -q     # 仅 integration 层
uv run pytest -m evaluation -q      # 仅 evaluation 层
uv run python -m evals.runner
openspec-cn validate <change-name> --strict
```

| 项 | 说明 |
| --- | --- |
| 分层执行 | `-m unit` 跑纯函数（秒级）；`-m contract` 跑 Port 契约；`-m integration` 跑全链路 |
| 默认评测 | 只计算确定性 Recall@K 和安全通过率，不调用模型或 PostgreSQL |
| Recall 执行 | 通过公开生产 Application Service 和 InMemory Repository，不复制私有排序 |
| PostgreSQL 外部用例 | 未显式获得专用数据库时应安全 skip |
| 测试数量 | 以命令输出为准 |

## 3. PostgreSQL 安全边界

外部测试会迁移并清空 Memory 表。专用测试库必须满足：

| # | 要求 |
| --- | --- |
| 1 | database 名称含 `test` |
| 2 | 可清空的专用库，不与开发/生产共享 |
| 3 | 账号拥有 migration、DDL/DML 和 truncate 权限 |
| 4 | 操作者显式设置 `MEMORY_MCP_TEST_DATABASE_URL` |

```bash
MEMORY_MCP_TEST_DATABASE_URL='<专用测试库 DSN>' uv run pytest \
  tests/contract/test_postgresql_contract.py tests/integration/test_postgresql_transport.py -q

# 共享库只运行非破坏性命令
.venv/bin/memory-mcp-db migrate
.venv/bin/memory-mcp-db health
```

未满足检查时测试拒绝执行，不自动读取生产 `MEMORY_MCP_DATABASE_URL`。
`Memory PostgreSQL is healthy` 表示 migration checksum 一致、扩展可用且必需索引存在。

| 项 | 说明 |
| --- | --- |
| `0001_memory_schema.sql` | 安装 `pg_jieba`（中文分词全文检索）与 `vector` 扩展、创建全部表和索引 |
| `health` 检查 | migration checksum、扩展和七个必需索引（含 `memory_revisions_embedding_idx`、`memory_items_one_active_scope_idx`、`memory_captures_pending_idx`） |
| 改 schema | 直接修改该文件并用 `migrate --rebuild` 重建 |

## 4. 投研评测

```bash
uv run python -m evals.runner
uv run python -m evals.runner --live-model --output evals/results/<safe-result-name>.json
```

| 项 | 说明 |
| --- | --- |
| deterministic（离线） | 只计算确定性 Recall@K 和安全通过率，不调用模型或 PostgreSQL |
| live（真实模型） | 有效的 `MEMORY_MCP_MODEL_*` 配置，只使用进程内 Repository，不写 PostgreSQL |
| 结果文件 | 只包含模型/数据集版本、聚合指标、分类指标和失败 case ID |
| 不保存 | 输入正文、Token 或 API Key |
| 模型结果与分析 | 只在[投研记忆评测](evaluation.md)维护 |
| 当前 v4 离线 Recall | 15/15 |

## 5. 新增测试应放在哪个目录

| 测试类型 | 目录 | 说明 |
| --- | --- | --- |
| 纯函数/领域规则 | `tests/unit/` | 无 I/O，验证算法与不变量 |
| Port 契约（新 Repository/Extractor/Profile） | `tests/contract/` | 验证协议实现一致性 |
| 跨层集成（新 MCP 工具/新 Hook 路径） | `tests/integration/` | 验证多模块协作 |
| 端到端主链路 | `tests/end_to_end/` | Agent→Server 关键路径 |
| 质量评测 case | `tests/evaluation/` 或 `evals/cases.json` | 不与功能测试混合 |

## 6. 禁止的测试反模式

- Mock 领域对象自身（用真实 dataclass）；
- Mock 被测类内部私有方法；
- 用 `assert_called_once` 替代业务结果断言；
- Mock 绝大多数依赖后只验证调用次数；
- 测试依赖公网、真实模型或真实 Token；
- 在 `tests/support/` 复制准入/召回/生命周期算法；
- 生产代码导入 `tests/`；
- autouse fixture 隐藏关键测试输入；
- 用降低断言强度让测试通过；
- 删除测试掩盖生产 Bug（发现 Bug 时保留复现测试并报告）。

## 7. 发布检查

```bash
uv build --package memory-mcp --wheel
uv build --package memory-mcp-agent --wheel
```

| 确认项 | 说明 |
| --- | --- |
| Server wheel | 不含 `tests/`、`evals/` 或测试提取实现 |
| Agent wheel | 只含 `memory_mcp_agent` 和 `memory-mcp-hook` |
| Agent 运行依赖 | 不含 PostgreSQL、LangChain、模型 Provider 或 Server 包 |
| console script | Server 与 Agent 不相互泄漏 |

公网 HTTPS、ECS/RDS 网络、安全组、systemd 权限、滚动升级和回滚属于部署验收，
见[部署指南](deploy.md)。
