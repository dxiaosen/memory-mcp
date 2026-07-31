"""Run one framework-neutral Agent turn with automatic memory hooks."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from memory_mcp.hooks import (
    HookContext,
    HookedAgentRunner,
    MemoryHookBridge,
    MemoryHookSettings,
    MemoryMcpClient,
)


async def _agent(user_input: str, memory_context: str | None) -> str:
    """Replace this callable with the application's real Agent invocation."""

    if memory_context:
        return f"已结合长期记忆处理：{user_input}"
    return f"已处理：{user_input}"


async def _run(args: argparse.Namespace) -> None:
    settings = MemoryHookSettings(_env_file=args.env_file)
    context = HookContext(
        scenario=settings.scenario,
        conversation_id=args.conversation_id,
        turn_id=args.turn_id,
        subject=args.subject,
        task_intent=args.task_intent,
    )
    async with MemoryMcpClient(settings) as client:
        bridge = MemoryHookBridge(client, settings)
        result = await HookedAgentRunner(bridge, _agent).run(
            context,
            args.input,
        )
    print(
        json.dumps(
            {
                "conversation_id": context.conversation_id,
                "turn_id": context.turn_id,
                "recalled_count": result.before_run.recalled_count,
                "memory_context": result.before_run.memory_context,
                "recall_warning": result.before_run.warning_code,
                "final_output": result.final_output,
                "capture_status": result.after_run.status,
                "capture_attempts": result.after_run.attempts,
                "capture_replayed": result.after_run.replayed,
                "created_memory_ids": result.after_run.created_memory_ids,
                "capture_warning": result.after_run.warning_code,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a top-level Agent turn with Memory MCP hooks."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help=(
            "Optional Agent environment file. Without it, MEMORY_HOOK_* "
            "is read from the process environment."
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
