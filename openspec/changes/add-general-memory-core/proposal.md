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
- 本期正式部署目标为 Linux 云服务器上的单实例 MCP 服务：可信私网内直接访问
  `/mcp`，公网接入时由云负载均衡器提供 HTTPS；服务通过私网连接托管
  PostgreSQL，不引入 Docker 或 Nginx。
- 提供面向 Hook 的 MCP 工具，覆盖完成轮次捕获、任务前召回、当前记忆查看、
  待确认项查看、确认和拒绝。
- 定义运行前 Recall Hook 与运行后 Capture Hook。Hook 以程序方式调用 MCP
  工具，不依赖模型自行决定是否读取或保存记忆。
- 阶段五交付可直接运行的服务端候选抽取配置：生产运行时只使用真实
  OpenAI-compatible 结构化模型；确定性 fixed adapter 仅由自动化测试通过依赖
  注入使用，不再暴露运行时切换和候选 JSON 配置；Hook 闭环不再以
  `capture_not_configured` 为正常结束状态。
- 将环境配置、静态 Token 映射和 Principal 类型收敛为中性运行时命名，不在正式
  配置字段中使用 `demo` 或 `test`；同时明确静态 Token 认证仍是可替换的原型
  边界，不等同于生产 OAuth/OIDC。
- 将 Memory MCP Server 与 Agent Host 视为独立部署单元：服务端模板只包含数据库、
  HTTP、认证、抽取和日志配置；Agent Host 使用自己的 `MEMORY_HOOK_*` 配置，
  多身份验收配置和 fixed candidate 夹具不得混入生产模板。
- 将候选抽取所使用的模型配置统一到面向运维的 `MEMORY_MCP_MODEL_*` 命名空间；
  静态 Token 映射入口统一为 `MEMORY_MCP_AUTH_TOKENS`；生产进程始终要求真实
  模型配置，fixed candidate 只存在于测试代码。
- 增加显式内容日志模式，供受控环境手工观察完成轮次、模型候选、准入决定、
  持久化结果和召回上下文；默认关闭，启用时允许日志包含应用正文。
- owner 不再由 MCP 工具参数、模型输出、Token 配置或普通请求正文指定；服务端
  必须从可信 tenant/subject 身份确定性派生 owner。真实 OAuth/OIDC 适配器记录
  已验证的 client_id；当前静态 Token 适配器只记录凭据摘要形成的匿名 client
  引用，不再维护含义重复的 agent_id。
- 统一源码开发者注释和 docstring 的中文表达；外部协议字段、MCP 工具说明、
  模型提示、错误码和第三方标识继续保留其稳定英文形式。
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

- `memory_mcp.core.domain`、`application` 和 `ports` 继续作为 MCP 服务内部
  核心，前两阶段领域实现不推翻；SQLite Repository 作为已完成阶段的原型实现，
  在 PostgreSQL 契约与迁移测试通过后退出正式运行路径。
- 项目与 Python 包统一使用 `memory-mcp` / `memory_mcp`，核心位于 `core`，
  认证后的 MCP transport 与组合根位于 `server`，避免继续保留已经退出产品边界
  的 `agent-lab` 总称和 `memory_mcp.memory_mcp` 式重复命名。
- 新增 MCP Server 入口、MCP 协议 DTO、认证上下文适配器和远程 Hook Client；
  演示客户端只保留验证跨 Agent 接入所需的最小代码。
- 旧 Knowledge Agent、知识库索引、Chroma/Embedding、RAG CLI 和对应依赖不再是
  本课题产品能力；在 MCP Server 与最小演示客户端接管入口后删除。结构化抽取仍
  需要的通用模型工厂先提取到独立 adapter，避免随旧产品线误删。
- `PrincipalContext` 的构造边界从本地调用方移动到服务端认证适配器；内部
  `owner_id` 继续作为存储隔离键，但不得直接信任 MCP 入参。
- 项目增加官方 MCP Python SDK、PostgreSQL 驱动和连接池依赖。Linux 云服务器
  直接通过 Python 环境与 systemd 运行，不要求 Docker 或 Nginx；公网 HTTPS
  由云负载均衡器终止。
- PostgreSQL 是部署环境的唯一权威存储；Embedding、向量数据库和搜索引擎仍不
  属于本期。任何未来二级检索索引都不得成为身份授权或生命周期状态的事实源。
- 自动化测试和现场演示改为围绕“Agent A 捕获、Agent B 召回、Agent C/另一用户
  不可见”的跨 Agent 数据流组织。
- 阶段五同时交付框架无关 Hook Runner、两个独立客户端配置和可重复的本地
  PostgreSQL 端到端流程；公网 HTTPS 与安全组边界保留为部署环境验收。
- 服务端配置模板按数据库、HTTP、认证、模型和日志分区；静态认证使用
  `AUTH_TOKENS` 等中性名称，内容日志由独立开关控制。Agent 集成提供独立
  模板并只使用 `MEMORY_HOOK_*`，不再让服务端 `.env` 同时承担多 Agent 验收配置。
- 在进入阶段六前收敛阶段五实现结构：Hook 使用异步 I/O 但不引入消息队列，
  增加有界去重、payload 冲突检测、连接复用和完整 capture receipt；把真实/固定
  模型适配器统一到语义明确的 `extraction` 包（固定适配器仅供测试注入），并清理
  有副作用的包入口、反向依赖和过长的捕获/Repository 内部职责。
