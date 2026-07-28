"""把知识库检索能力封装为 LangChain Tool。"""

from langchain_core.prompts import PromptTemplate
from langchain_core.tools import BaseTool
from langchain_core.tools.retriever import create_retriever_tool

from .ports import KnowledgeStore

_DOCUMENT_PROMPT = PromptTemplate.from_template(
    "来源：{source}；页码：{page}\n{page_content}"
)


def create_knowledge_search_tool(
    store: KnowledgeStore,
    *,
    top_k: int,
) -> BaseTool:
    """创建供 Agent 调用的知识库检索工具。"""

    retriever = store.as_retriever(top_k=top_k)
    return create_retriever_tool(
        retriever,
        name="search_knowledge_base",
        description=(
            "检索已建立索引的业务知识库。回答涉及业务文档、制度、产品资料或"
            "内部知识的问题前必须先调用本工具。如果结果不足，应明确说明缺少依据。"
        ),
        document_prompt=_DOCUMENT_PROMPT,
        document_separator="\n\n---\n\n",
        response_format="content_and_artifact",
    )
