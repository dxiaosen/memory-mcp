"""实现知识库索引和问答命令行。"""

import argparse
import logging
from collections.abc import Sequence
from uuid import uuid4

from pydantic import ValidationError

from agent_lab.agents import AgentResponse
from agent_lab.bootstrap import build_agent_service, build_knowledge_indexer
from agent_lab.config import (
    AgentSettings,
    KnowledgeSettings,
    get_knowledge_settings,
    get_settings,
)
from agent_lab.exceptions import AgentLabError
from agent_lab.observability import configure_logging_from_settings, log_event

_LOGGER = logging.getLogger(__name__)


def main() -> None:
    """解析命令行参数并执行对应子命令。"""

    parser = _create_parser()
    args = parser.parse_args()

    try:
        if args.command == "index":
            settings = get_knowledge_settings()
        else:
            settings = get_settings()
        configure_logging_from_settings(settings)
        log_event(
            _LOGGER,
            logging.INFO,
            "cli.command.started",
            command=args.command,
        )
        if args.command == "index":
            _run_index_command(
                get_knowledge_settings(),
                args.paths,
                rebuild=args.rebuild,
            )
        elif args.command == "chat":
            _run_chat_command(
                get_settings(),
                args.prompt,
                thread_id=args.thread_id,
            )
        else:
            parser.error(f"Unknown command: {args.command}")
        log_event(
            _LOGGER,
            logging.INFO,
            "cli.command.completed",
            command=args.command,
        )
    except ValidationError as exc:
        parser.error(_configuration_error_message(exc))
    except (AgentLabError, OSError, ValueError) as exc:
        log_event(
            _LOGGER,
            logging.ERROR,
            "cli.command.failed",
            command=args.command,
            error_type=type(exc).__name__,
        )
        parser.exit(1, f"错误：{exc}\n")
    # 不同模型 SDK 的异常体系并不统一；CLI 作为最终进程边界，
    # 应记录完整日志，同时只向用户展示简洁错误信息。
    except Exception as exc:
        _LOGGER.exception(
            'event="cli.command.unexpected_failure" command=%r error_type=%r',
            args.command,
            type(exc).__name__,
        )
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
    settings: KnowledgeSettings,
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
    settings: AgentSettings,
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
            _LOGGER.exception(
                'event="cli.agent_turn.failed" error_type=%r',
                type(exc).__name__,
            )
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
