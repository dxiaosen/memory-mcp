"""Minimal authenticated client for the remote Memory MCP service."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from memory_mcp.hooks import MemoryHookSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        help=(
            "Optional Agent environment file. Without it, MEMORY_MCP_URL "
            "and MEMORY_MCP_TOKEN are read from the process environment."
        ),
    )
    parser.add_argument(
        "command",
        choices=("tools", "memories", "pending", "recall"),
        help="Read-only operation to execute",
    )
    parser.add_argument("--scenario", default="general-work")
    parser.add_argument("--query", default="当前任务相关的用户偏好和上下文")
    parser.add_argument("--subject")
    return parser


def _structured_payload(result: Any) -> dict[str, Any]:
    payload = result.structuredContent
    if not isinstance(payload, dict):
        return {"is_error": result.isError, "content": str(result.content)}
    nested = payload.get("result")
    return nested if isinstance(nested, dict) else payload


async def _run(
    url: str,
    token: str,
    command: str,
    *,
    scenario: str,
    query: str,
    subject: str | None,
) -> None:
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    ) as client:
        async with streamable_http_client(
            url,
            http_client=client,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                if command == "tools":
                    result = await session.list_tools()
                    output: object = [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "input_schema": tool.inputSchema,
                        }
                        for tool in result.tools
                    ]
                elif command == "recall":
                    result = await session.call_tool(
                        "recall_memory",
                        arguments={
                            "scenario": scenario,
                            "query": query,
                            "subject": subject,
                        },
                    )
                    output = _structured_payload(result)
                else:
                    tool_name = (
                        "list_memories"
                        if command == "memories"
                        else "list_pending_reviews"
                    )
                    result = await session.call_tool(tool_name, arguments={})
                    output = _structured_payload(result)
                print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    args = _parser().parse_args()
    settings = MemoryHookSettings(_env_file=args.env_file)
    asyncio.run(
        _run(
            str(settings.mcp_url),
            settings.token_value(),
            args.command,
            scenario=args.scenario,
            query=args.query,
            subject=args.subject,
        )
    )


if __name__ == "__main__":
    main()
