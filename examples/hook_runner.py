"""使用主动记忆 Hook 运行一个框架无关的 Agent 顶层轮次（BeforeRun 召回）。

Phase 1（模型自主调用 capture）后，Hook 只保留 BeforeRun 召回注入；AfterRun
capture 由模型自主调用 ``capture_completed_turn`` MCP 工具，不再经本脚本。
本示例展示 BeforeRun 召回链：从 Memory MCP 召回相关记忆并注入上下文。
"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from memory_mcp_agent import (
    HookContext,
    MemoryHookBridge,
    MemoryHookSettings,
    MemoryMcpClient,
)


async def _run(args: argparse.Namespace) -> None:
    settings = MemoryHookSettings(_env_file=args.env_file)
    context = HookContext(
        profile_id=settings.profile_id,
        conversation_id=args.conversation_id,
        turn_id=args.turn_id,
        subject=args.subject,
        task_intent=args.task_intent,
    )
    async with MemoryMcpClient(settings) as client:
        bridge = MemoryHookBridge(client, settings)
        result = await bridge.before_run(context, args.input)
    print(
        json.dumps(
            {
                "conversation_id": context.conversation_id,
                "turn_id": context.turn_id,
                "recalled_count": result.recalled_count,
                "memory_context": result.memory_context,
                "truncated": result.truncated,
                "warning_code": result.warning_code,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a top-level Agent turn with Memory MCP BeforeRun recall."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help=(
            "Optional Agent environment file. Without it, MEMORY_MCP_URL "
            "and MEMORY_MCP_TOKEN are read from the process environment."
        ),
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--conversation-id", default=f"run-{uuid4()}")
    parser.add_argument("--turn-id", default=f"turn-{uuid4()}")
    parser.add_argument("--subject")
    parser.add_argument("--task-intent")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
