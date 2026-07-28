"""实现知识库索引和问答命令行。"""

import argparse
import logging
from collections.abc import Sequence
from uuid import uuid4

from pydantic import ValidationError

from agent_lab.agents import AgentResponse
from agent_lab.bootstrap import build_agent_service, build_knowledge_indexer
from agent_lab.config import Settings, get_settings
from agent_lab.exceptions import AgentLabError


def main() -> None:
    """解析命令行参数并执行对应子命令。"""

    parser = _create_parser()
    args = parser.parse_args()

    try:
        settings = get_settings()
        _configure_logging(settings)
        if args.command == "index":
            _run_index_command(settings, args.paths, rebuild=args.rebuild)
        elif args.command == "chat":
            _run_chat_command(settings, args.prompt, thread_id=args.thread_id)
        else:
            parser.error(f"Unknown command: {args.command}")
    except ValidationError as exc:
        parser.error(_configuration_error_message(exc))
    except (AgentLabError, OSError, ValueError) as exc:
        parser.exit(1, f"错误：{exc}\n")
    # 不同模型 SDK 的异常体系并不统一；CLI 作为最终进程边界，
    # 应记录完整日志，同时只向用户展示简洁错误信息。
    except Exception as exc:
        logging.getLogger(__name__).exception("Unexpected application failure")
        parser.exit(1, f"执行失败：{exc}\n")


def _create_parser() -> argparse.ArgumentParser:
    """创建包含索引和问答子命令的参数解析器。"""

    parser = argparse.ArgumentParser(description="基于 LangChain 的知识库 Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser(
        "index",
        help="加载文档并写入持久化向量库",
    )
    index_parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="支持 .txt、.md、.markdown、.pdf 文件或目录",
    )
    index_parser.add_argument(
        "--rebuild",
        action="store_true",
        help="先清空当前 collection，再重新建立索引",
    )

    chat_parser = subparsers.add_parser(
        "chat",
        help="使用已经建立索引的知识库进行问答",
    )
    chat_parser.add_argument(
        "prompt",
        nargs="?",
        help="单次提问；省略后进入交互模式",
    )
    chat_parser.add_argument(
        "--thread-id",
        default=None,
        help="可选会话标识；默认生成随机标识",
    )
    return parser


def _run_index_command(
    settings: Settings,
    paths: Sequence[str],
    *,
    rebuild: bool,
) -> None:
    """执行知识文档索引命令。"""

    report = build_knowledge_indexer(settings).index(paths, rebuild=rebuild)
    print(
        "索引完成："
        f"{report.source_file_count} 个文件，"
        f"{report.source_document_count} 个原始文档，"
        f"{report.chunk_count} 个文本块；"
        f"向量库当前共 {report.stored_chunk_count} 个文本块。"
    )


def _run_chat_command(
    settings: Settings,
    prompt: str | None,
    *,
    thread_id: str | None,
) -> None:
    """执行单轮问答或启动交互式会话。"""

    service = build_agent_service(settings)
    active_thread_id = thread_id or uuid4().hex

    if prompt:
        _print_response(service.run(prompt, thread_id=active_thread_id))
        return

    print("知识库 Agent 已启动。输入 /reset 清空当前会话，/exit 退出。")
    while True:
        try:
            user_input = input("你> ").strip()
        except EOFError, KeyboardInterrupt:
            print()
            break

        if not user_input:
            continue
        if user_input.casefold() in {"/exit", "/quit"}:
            break
        if user_input.casefold() == "/reset":
            service.clear_thread(active_thread_id)
            active_thread_id = uuid4().hex
            print("当前会话已清空。")
            continue

        try:
            response = service.run(user_input, thread_id=active_thread_id)
        except Exception as exc:
            logging.getLogger(__name__).exception("Agent turn failed")
            print(f"Agent 执行失败：{exc}")
        else:
            _print_response(response, prefix="Agent> ")


def _print_response(response: AgentResponse, *, prefix: str = "") -> None:
    """输出 Agent 回答以及去重后的来源信息。"""

    print(f"{prefix}{response.answer}")
    if response.sources:
        print("来源：")
        seen: set[tuple[str, int | None]] = set()
        for citation in response.sources:
            key = (citation.source, citation.page)
            if key in seen:
                continue
            seen.add(key)
            page = f"，第 {citation.page} 页" if citation.page else ""
            print(f"- {citation.source}{page}")


def _configure_logging(settings: Settings) -> None:
    """根据应用配置初始化基础日志格式和级别。"""

    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _configuration_error_message(error: ValidationError) -> str:
    """将 Pydantic 配置异常转换为适合终端展示的信息。"""

    missing_fields = [
        str(item["loc"][0])
        for item in error.errors()
        if item["type"] == "missing" and item.get("loc")
    ]
    if missing_fields:
        fields = ", ".join(name.upper() for name in missing_fields)
        return f"缺少必要配置：{fields}。请参考 .env.example。"
    return f"配置无效：{error}"
