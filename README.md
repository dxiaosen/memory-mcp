# Agent Lab

一个面向知识库问答场景的工程化 Agent 基础项目，适合在明确业务需求前快速验证模型、工具和 RAG 流程。

项目基于：

- LangChain v1：Agent、工具、消息和 Retriever 抽象
- LangGraph：Agent 运行时与会话状态
- Chroma：本地持久化向量库
- LangChain DeepSeek/OpenAI integrations：供应商专用模型适配
- LangChain Text Splitters：适合中英文的递归文本切分

## 核心能力

- DeepSeek 和 OpenAI 模型供应商切换
- LangChain `create_agent` 工具调用循环
- `.txt`、`.md`、`.markdown`、`.pdf` 文档加载
- 独立的离线索引与在线问答流程
- Chroma 持久化向量索引
- 检索结果通过 `ToolMessage.artifact` 保留来源
- 多轮会话、来源展示和 token 使用统计
- Pydantic 配置与工具参数验证
- 针对中文标点优化的文本切分

## 项目结构

```text
src/agent_lab/
├── agents/
│   ├── factory.py          # LangChain Agent 装配
│   ├── prompts.py          # Agent 系统指令
│   ├── schemas.py          # AgentResponse、来源、token 统计
│   └── service.py          # 会话级应用服务
├── cli/
│   └── main.py             # index/chat 命令
├── config/
│   └── settings.py         # 环境配置和交叉校验
├── integrations/
│   ├── chat_models.py      # ChatDeepSeek / ChatOpenAI 工厂
│   ├── embeddings.py       # Embedding 模型工厂
│   └── vector_store.py     # Chroma 持久化适配
├── knowledge/
│   ├── indexer.py          # 文档切分和幂等索引
│   ├── loaders.py          # 文本、Markdown、PDF 加载
│   ├── ports.py            # KnowledgeStore 接口
│   ├── retrieval.py        # Retriever Tool
│   └── schemas.py          # 索引结果
├── bootstrap.py            # 依赖装配
└── exceptions.py           # 应用异常
```

依赖方向：

```text
CLI → Bootstrap → Agents / Knowledge
                    ↓          ↓
                 LangChain   KnowledgeStore port
                                 ↑
                         Chroma integration
```

业务模块不直接创建 SDK Client，也不直接读取环境变量。模型、Embedding 和向量库的创建集中在 `integrations` 与 `bootstrap`。

## 快速开始

项目当前使用 Python 3.14 和 [uv](https://docs.astral.sh/uv/)。

```powershell
Copy-Item .env.example .env
uv sync
```

编辑 `.env`，至少配置聊天模型和 Embedding 模型：

```dotenv
CHAT_MODEL_PROVIDER=deepseek
CHAT_MODEL_NAME=deepseek-v4-flash
CHAT_MODEL_API_KEY=sk-xxxxxxxx
CHAT_MODEL_BASE_URL=https://api.deepseek.com

EMBEDDING_MODEL_PROVIDER=openai
EMBEDDING_MODEL_NAME=your-embedding-model
EMBEDDING_MODEL_API_KEY=sk-xxxxxxxx
EMBEDDING_MODEL_BASE_URL=https://your-approved-embedding-endpoint/v1
```

DeepSeek 当前不提供通用 Embeddings 接口，因此聊天模型和 Embedding 服务需要分别配置。

### 1. 建立知识索引

```powershell
uv run agent-lab index ./knowledge --rebuild
```

支持一次传入多个文件或目录：

```powershell
uv run agent-lab index ./knowledge/policy.pdf ./knowledge/product.md
```

不指定 `--rebuild` 时，同一来源文档的旧文本块会被替换，不会重复累积。

### 2. 交互问答

```powershell
uv run agent-lab chat
```

单次问答：

```powershell
uv run agent-lab chat "这份制度对审批时限有什么要求？"
```

交互模式支持：

- `/reset`：清空当前进程中的对话状态
- `/exit`：退出

Chroma 索引默认保存在 `.agent-lab/chroma`，不会提交到 Git。

## 配置

### Chat model

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CHAT_MODEL_PROVIDER` | `deepseek` | `deepseek` 或 `openai` |
| `CHAT_MODEL_NAME` | 必填 | 模型名称 |
| `CHAT_MODEL_API_KEY` | 必填 | 模型 API Key |
| `CHAT_MODEL_BASE_URL` | 供应商默认值 | 自定义 API 地址 |
| `CHAT_MODEL_TEMPERATURE` | `0` | 生成温度 |
| `CHAT_MODEL_TIMEOUT_SECONDS` | `60` | 请求超时 |
| `CHAT_MODEL_MAX_RETRIES` | `2` | 最大重试次数 |

DeepSeek 使用官方 `ChatDeepSeek` 集成，OpenAI 使用 `ChatOpenAI`。不要用 `ChatOpenAI(base_url=DeepSeek)` 替代 `ChatDeepSeek`，否则 DeepSeek 的非标准推理字段可能丢失。

### Embeddings and retrieval

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EMBEDDING_MODEL_PROVIDER` | `openai` | 当前支持 OpenAI-compatible Embeddings |
| `EMBEDDING_MODEL_NAME` | 必填 | Embedding 模型名称 |
| `EMBEDDING_MODEL_API_KEY` | 必填 | Embedding API Key |
| `EMBEDDING_MODEL_BASE_URL` | 供应商默认值 | Embeddings API 地址 |
| `VECTOR_STORE_PERSIST_DIRECTORY` | `.agent-lab/chroma` | Chroma 数据目录 |
| `VECTOR_STORE_COLLECTION_NAME` | `agent-lab-knowledge` | Collection 名称 |
| `DOCUMENT_CHUNK_SIZE` | `800` | 文本块字符数 |
| `DOCUMENT_CHUNK_OVERLAP` | `120` | 文本块重叠字符数 |
| `RETRIEVAL_TOP_K` | `4` | 单次召回数量 |

### Agent runtime

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENT_RECURSION_LIMIT` | `12` | 单轮图执行最大递归步数 |
| `LOG_LEVEL` | `INFO` | 应用日志级别 |

## 增加业务逻辑

普通业务能力应实现为带类型提示和 docstring 的 LangChain Tool：

```python
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class FundLookupInput(BaseModel):
    fund_code: str = Field(pattern=r"^\d{6}$")


@tool(args_schema=FundLookupInput)
def lookup_fund(fund_code: str) -> dict[str, str]:
    """根据基金代码查询已经授权访问的基金基础信息。"""

    return {"fund_code": fund_code, "name": "示例基金"}
```

然后在 `create_agent_service(..., additional_tools=[lookup_fund])` 中注入。业务 Tool 内必须完成权限、参数和数据范围校验，不能依赖模型自行保证安全。

新增模型供应商时，在 `integrations/chat_models.py` 增加一个明确的 provider 分支，并安装该供应商的 LangChain 官方 integration；不要在业务代码中判断供应商。

## 测试

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pytest
```

测试不调用外部模型或 Embedding API。Chroma 使用 LangChain 的 deterministic fake embeddings 完成离线契约测试。

## 数据安全

- 未经明确授权，不要把内部报告、客户信息或非公开数据发送到外部模型服务。
- 前期使用公开或模拟文档验证流程。
- 检索到的文档内容是不可信输入；系统提示已要求模型不得执行文档中的指令。
- 涉及写操作、对外发送、交易或投资决策的工具，应增加人工审批。

更多设计取舍见 [docs/architecture.md](docs/architecture.md)。
