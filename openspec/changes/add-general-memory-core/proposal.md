## Why

不同 Agent Runtime 通常各自保存会话状态，无法在用户授权范围内共享长期记忆；
即使某个 Agent 内部实现了记忆，也很难被 Codex、LangChain Agent 或其他客户端
复用。本项目因此不再以“给现有 Agent 增加一个内嵌记忆模块”为交付目标，而是
建设一个独立、可远程接入的主动记忆 MCP 服务。

该服务通过标准 MCP 工具提供捕获、召回和用户治理能力；Agent 侧只需在运行前后
安装轻量 Hook，即可在回答前主动召回当前用户的有效记忆，并在一轮任务完成后
提交本轮信息。这样可以用同一套记忆生命周期支撑多个 Agent，同时把身份隔离、
敏感拦截、准入和版本规则集中在服务端执行。

## What Changes

- **BREAKING**：MCP Server 成为本课题唯一正式产品形态，不再把 Memory Core
  作为某个 Agent 进程内的可选模块交付。
- 增加远程 MCP 接入边界，优先使用 Streamable HTTP；stdio 只用于本地调试，
  不作为“对外服务”演示形态。
- 将服务定义为平台无关的公网 MCP Resource Server。Codex、LangChain、自研
  Agent 或其他兼容客户端可直接通过 URL 和认证信息接入；阿里云百炼等平台只
  是可选客户端，不构成运行依赖。
- 本期正式部署目标为 Linux 云服务器上的单实例 MCP 服务，通过独立 HTTPS
  终止层暴露 `/mcp`，并通过私网连接托管 PostgreSQL。Docker、Nginx 和特定云
  网关都不是协议依赖，部署时按环境选择。
- 提供面向 Hook 的 MCP 工具，覆盖完成轮次捕获、任务前召回、当前记忆查看、
  待确认项查看、确认和拒绝。
- 定义运行前 Recall Hook 与运行后 Capture Hook。Hook 以程序方式调用 MCP
  工具，不依赖模型自行决定是否读取或保存记忆。
- owner 不再由 MCP 工具参数、模型输出或普通请求正文指定；服务端必须从可信
  认证上下文得到当前用户范围，并同时记录调用方 Agent 身份。
- 复用已经完成的通用记忆卡片、来源、场景策略、四类准入、敏感拦截、幂等捕获
  和 pending 流程，在其外增加 MCP transport、协议 DTO 和 Agent Hook adapter。
- 将 `TurnEnvelope` 演进为框架无关的完成轮次事件，区分用户输入、Agent 输出和
  可选工具观察；只有可信来源块能够成为自动保存的用户表达证据。
- 为跨 Agent 演示实现最小生命周期闭环：重复表达合并来源、用户明确替代旧记忆、
  当前有效记忆召回和历史排除。
- 20 个工作日内只完成可运行、可接入、可验证和可现场演示的竖向闭环。
- 将已经验证过的 SQLite 原型存储迁移为 PostgreSQL 正式运行后端，以支持独立
  服务部署、连接池、托管备份和后续多进程演进；不为两套生产存储长期维护重复
  实现。
- 将“完整目标模型”和“20 天实现面”分层：身份、来源、时间、生命周期、业务进度、
  策略版本和关系扩展等有长期价值的语义继续保留；本期只激活演示闭环实际需要的
  子集，不以删字段伪装范围收敛。
- 从本期 P0 实现范围移出复杂向量检索、通用关系图、自动过期调度、完整删除抑制、
  第二正式业务场景、大规模对比评测和重型管理界面；保留相应端口或数据语义的
  演进位置，但不为未验证需求预建运行时。

## Capabilities

### New Capabilities

- `memory-capture`：Agent Hook 通过 MCP 提交已完成轮次，服务端完成敏感预检、
  原子候选发现、四类准入、幂等处理和待确认分流。
- `memory-lifecycle`：服务端维护跨 Agent 共享的当前记忆、来源、重复强化、明确
  替代和历史排除，不把生命周期决策下放给 Agent。
- `memory-recall`：Agent Hook 在任务开始前通过 MCP 按可信用户、场景、对象和
  当前任务召回少量当前有效记忆，并获得可注入模型上下文的结构化结果。
- `memory-governance`：服务端统一执行可信身份映射、跨用户隔离、敏感持久化
  边界、待确认项管理、调用方审计和原型身份能力声明。

### Modified Capabilities

无。当前 OpenSpec 主规范中尚无已归档的部署能力。

## Impact

- `agent_lab.memory.domain`、`application` 和 `ports` 继续作为 MCP 服务内部
  核心，前两阶段领域实现不推翻；SQLite Repository 作为已完成阶段的原型实现，
  在 PostgreSQL 契约与迁移测试通过后退出正式运行路径。
- 新增 MCP Server 入口、MCP 协议 DTO、认证上下文适配器和远程 Hook Client；
  演示客户端只保留验证跨 Agent 接入所需的最小代码。
- 旧 Knowledge Agent、知识库索引、Chroma/Embedding、RAG CLI 和对应依赖不再是
  本课题产品能力；在 MCP Server 与最小演示客户端接管入口后删除。结构化抽取仍
  需要的通用模型工厂先提取到独立 adapter，避免随旧产品线误删。
- `PrincipalContext` 的构造边界从本地调用方移动到服务端认证适配器；内部
  `owner_id` 继续作为存储隔离键，但不得直接信任 MCP 入参。
- 项目增加官方 MCP Python SDK、PostgreSQL 驱动和连接池依赖。Linux 云服务器
  直接通过 Python 环境与 systemd 运行，不要求 Docker；公网 HTTPS 可由 Nginx、
  云负载均衡或其他反向代理终止。
- PostgreSQL 是部署环境的唯一权威存储；Embedding、向量数据库和搜索引擎仍不
  属于本期。任何未来二级检索索引都不得成为身份授权或生命周期状态的事实源。
- 自动化测试和现场演示改为围绕“Agent A 捕获、Agent B 召回、Agent C/另一用户
  不可见”的跨 Agent 数据流组织。
