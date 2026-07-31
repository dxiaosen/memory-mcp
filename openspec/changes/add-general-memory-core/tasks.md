## 1. 阶段一——通用契约、可信记忆卡片和用户隔离（D1～D4，已完成）

- [x] 1.1 固化通用 Core 与记忆配置的职责边界、依赖方向和阶段一验收清单
- [x] 1.2 建立 Memory Core 模块边界，并定义最小 `MemoryProfile`、profile 注册表和安全失败行为
- [x] 1.3 建立无需外部服务的 SQLite 开发环境、`PRAGMA quick_check` 健康检查和版本化初始迁移
- [x] 1.4 实现 `MemoryItem`、初始 `MemoryRevision`、`Evidence`、assertion kind 和通用有效状态
- [x] 1.5 定义可信 `PrincipalContext`，并实现所有公开操作都按 owner 限定的手动创建、列表和详情查询
- [x] 1.6 增加 owner、来源、合法状态、已注册 profile 和单一当前 revision 的数据库约束
- [x] 1.7 实现测试 profile 和中性项目协作记忆夹具
- [x] 1.8 增加非法记录、非法 profile、非法类型、identifier 猜测和跨用户读写的离线测试
- [x] 1.9 增加 Core 不导入正式 profile 的依赖守卫，演示手动创建与隔离并记录阶段一验收结果

## 2. 阶段二——通用捕获、准入和待确认流程（D5～D8，已完成）

- [x] 2.1 定义 `TurnEnvelope`、结构化 `Candidate`、`AdmissionDecision`、`ReviewItem` 和 `CaptureResult`
- [x] 2.2 让捕获流程从当前 `MemoryProfile` 获取合法类型和抽取提示，并定义原子候选结构
- [x] 2.3 实现模型调用和持久化前的禁止内容检测、脱敏及禁止正文不落库边界
- [x] 2.4 实现结构化模型适配器，并记录 prompt、schema、model 和 profile 版本
- [x] 2.5 校验模型输出，并使用可信值覆盖模型提供的 owner、source id 和观察时间
- [x] 2.6 实现自动保存、待确认、丢弃和敏感拦截四种互斥准入规则及同步持久化
- [x] 2.7 实现 source turn 幂等，重试不得重复生成候选、待确认项、Evidence 或活动记忆
- [x] 2.8 实现 pending 内容的 owner 范围查看、确认和拒绝，并保证确认前不可召回
- [x] 2.9 完成多候选、临时指令、弱推断、相对时间、用户观点、敏感文本、重试和多策略测试

## 3. 阶段三——远程 MCP 服务与可信身份边界（D9～D12，已完成）

- [x] 3.1 锁定官方 MCP Python SDK，建立 `MemoryServerSettings`、`memory_mcp` 包、Streamable HTTP `/mcp` 入口和健康检查
- [x] 3.2 定义 `RequestPrincipal`，实现演示 token 到 owner/client/scopes 的可信映射与 read/write/review 权限检查
- [x] 3.3 定义版本化 `CompletedTurnEventV1`、角色消息块、payload fingerprint、结构化结果 DTO 和稳定错误码
- [x] 3.4 实现 `capture_completed_turn` 及 list/get/pending/confirm/reject MCP 工具，将可信 principal 映射到现有应用服务
- [x] 3.5 实现 request id、无正文操作日志、同 event replay 和不同 payload conflict
- [x] 3.6 完成 owner 字段拒绝、无认证、缺少 scope、跨用户 identifier、敏感响应、幂等和 SQLite 重开的 transport 测试
- [x] 3.7 使用真实 MCP Client 和 MCP Inspector 远程重放阶段二案例，记录 MCP 阶段验收结果
- [x] 3.8 在 MCP 入口和最小演示客户端可替代旧入口后，提取仍需复用的结构化模型 adapter，删除 `agents`、`knowledge`、旧 `cli/bootstrap`、Embedding/Chroma 集成、RAG 依赖和对应测试；全量 Memory/MCP 测试继续通过
- [x] 3.9 完成阶段四前收尾：统一 `memory-mcp` / `memory_mcp`、`core` 和 transport 命名；让所有候选持久化字段经过敏感检查；禁止 backend 异常正文进入日志；避免同步 Core/Repository 调用阻塞 MCP 事件循环；合并阶段一至三重复文档并完成回归验证

## 4. 阶段四——PostgreSQL、最小生命周期与主动召回（D13～D15）

- [x] 4.1 建立 PostgreSQL 版本化 schema、显式 migration、连接池、健康检查和 secret-backed `database_url`
- [x] 4.2 实现 PostgreSQL `MemoryRepository`，保持 owner、捕获幂等、pending resolution 和单一 current 的事务约束
- [x] 4.3 复用同一组 Repository contract cases 在真实 PostgreSQL 验证 owner、事务、review resolution、event/source-turn 幂等、重叠重试、migration checksum、连接池关闭和 MCP 重启；通过后删除 SQLite 正式运行路径、adapter、migration 和专项测试
- [x] 4.4 实现并注册正式 `GeneralWorkProfile`，包含 preference、stable_context、ongoing_item 和 decision
- [x] 4.5 增加 duplicate Evidence、同一 MemoryItem 内 replacement revision、superseded history 和单一 current 所需的 migration、Repository port 与事务
- [x] 4.6 定义最小确定性文本规范化；实现同 scope duplicate、明确用户 replacement、其余冲突 pending 的分类，并由程序选择可信目标 memory/revision
- [x] 4.7 实现 owner-first 结构化 recall query port，在 Repository 边界先按 owner、active/current、profile_id 和 subject 缩小候选，再按 task intent、阈值、数量和 token budget 过滤
- [x] 4.8 实现 `recall_memory` MCP 工具，返回结构化 revision/source 和安全 `rendered_context`
- [x] 4.9 完成 duplicate、replacement、pending/history 排除、空召回、当前指令优先、跨用户召回测试和三轮远程示例

## 5. 阶段五——Agent Client、Linux 部署与跨 Agent 接入（D16～D17）

- [x] 5.1 建立独立轻量 `memory-mcp-agent` 发行包、`MemoryHookSettings`、单一远程 `MemoryMcpClient`、框架无关 HookContext 和可供无原生 Hook Host 调用的 Hook Bridge；Server 生产包不提供 Hook 命令
- [x] 5.2 实现 BeforeRun Hook：每个用户任务只召回一次，空结果不注入，失败安全继续
- [x] 5.3 实现 AfterRun Hook：只提交成功完成轮次，生成稳定 event id，并用同一 id 有限重试
- [x] 5.4 接入一个真实 OpenAI-compatible 结构化模型 backend，在默认服务组合根按配置创建 `CandidateExtractor`，同时保留固定离线 backend、确定性夹具和安全启动失败
- [x] 5.5 增加 `uv + systemd` Linux 部署单元、独立 migration 命令、私网直连/可选云负载均衡 HTTPS 和 ECS 发布/回滚说明，不引入 Docker 或 Nginx
- [x] 5.6 接入两个平台无关的独立 Runner/客户端配置，真实 Agent Host 只作为兼容性 smoke path；生成阶段五整体设计、测试说明和端到端使用文档
- [x] 5.7 使用真实 MCP HTTP、固定 backend 和 PostgreSQL 完成“用户 A / Agent A 写入、用户 A / Agent B 召回、用户 B / Agent B 不可见”的自动化端到端验收，并提供真实模型手工 smoke 步骤
- [x] 5.8 强化 Hook 异步边界：BeforeRun 前台 await、AfterRun 默认 await receipt、不引入外部队列；实现有界 run cache、payload fingerprint 冲突、可复用 HTTP Client 生命周期、完整 capture summary/failure 返回和对应并发/关闭测试
- [x] 5.9 将 `chat_models.py`、`model_extraction.py` 和模型专用 settings 收敛到 `extraction/` 包，删除未使用日志 settings/getter；让组合根/adapter 包入口保持轻量，移出 PostgreSQL adapter 对 Server Settings 的反向 CLI 依赖，并整理测试 support/import
- [x] 5.10 在不改变公开契约和事务边界的前提下拆分 CaptureService 的候选处理/Review 协调，以及 PostgreSQL Repository 的 row mapping/write validation；保留公开 facade，补依赖守卫并完成全量与真实 PostgreSQL 回归

## 6. 阶段六——真实抽取、演示与交付（D18～D20）

- [ ] 6.1 在部署环境验证公网 HTTPS MCP、私网 PostgreSQL、应用端口不公开、secret 不落日志和进程参数
- [ ] 6.2 构造 10～15 个跨 Agent 脚本，覆盖四类准入、duplicate、replacement 和 empty recall
- [x] 6.3 测量 capture/recall 延迟，完成 schema、认证隔离、幂等、敏感边界、数据库重启和失败恢复测试
- [ ] 6.4 最终同步 README、文档导航、详细总设计、配置、测试、使用和部署文档；核对 OpenSpec、云端验收结果和现场演示口径
- [ ] 6.5 准备 5～7 分钟现场脚本与录屏，固化 token、真实模型配置检查、云服务检查和测试注入的确定性证据
- [ ] 6.6 运行格式、静态检查、全量测试、PostgreSQL/MCP 端到端测试和 OpenSpec strict validation，记录最终验收结果
- [x] 6.7 规范化数据库、服务、静态认证、抽取、日志和 Hook 配置；去除运行时 demo/test 命名；实现默认关闭的核心内容日志及手工启用说明
- [x] 6.8 按独立部署单元重构配置：服务端生产模板不含多 Agent 身份配置或 fixed 夹具，抽取配置收敛到单一服务端命名空间，Agent Host 只要求 `MEMORY_MCP_URL/TOKEN`（旧名称兼容），增加静态 Token 最低长度校验并迁移示例、文档与测试
- [x] 6.9 将面向部署者的候选抽取变量改为 `MEMORY_MCP_MODEL_*`，将静态映射入口缩短为 `MEMORY_MCP_AUTH_TOKENS`，并把生产模板与本地验收配置中的身份值统一为中性的 tenant/subject/owner/Agent ID
- [x] 6.10 删除运行时 fixed backend 与候选 JSON 配置，改为测试依赖注入；由 tenant/subject 派生 owner、由静态 Token 摘要派生审计 client 并删除冗余 agent_id；同步配置、测试、文档和核心源码中文注释
- [x] 6.11 将两个发行包整理为对称的 `server/agent` virtual workspace，移除包内重复 Server 层，将 scenario 契约和 PostgreSQL schema 迁移为 `profile_id/profile_version`，同步全部文档并完成回归

## 明确延期，不进入本期任务

- 向量检索、Embedding、HNSW 和混合检索调优；
- supplement/correction/conflict 的完整关系矩阵和复杂关系图；
- 自动过期调度、完整删除抑制、合规级审计和 usage 全链路；
- 投资假设与调研问题两套正式 profile；
- 大规模无记忆/朴素摘要/主动记忆对比实验；
- Web 管理后台、MCP Apps、消息队列和异步任务；
- 多 worker 自动伸缩、数据库级 RLS、生产 OAuth 授权服务器和组织级多租户；
- Docker、ACK/Kubernetes 和特定 Agent 平台专用适配；
- SQLite 到 PostgreSQL 的通用存量数据迁移产品。

延期表示“本期不激活”，不表示删除已经存在且有明确演进用途的数据语义：
完整生命周期状态、原始/规范化时间、业务进度、策略版本、召回优先级和可选关系
策略继续保留；只有旧 RAG 产品线、重复入口和重复文档属于明确清理对象。
