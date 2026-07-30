"""Minimal authenticated client for the phase-three Memory MCP service."""

import argparse
import asyncio
import json
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8765/mcp",
        help="Streamable HTTP MCP endpoint",
    )
    parser.add_argument("--token", required=True, help="Prototype bearer token")
    parser.add_argument(
        "command",
        choices=("tools", "memories", "pending"),
        help="Read-only operation to demonstrate",
    )
    return parser


def _structured_payload(result: Any) -> dict[str, Any]:
    payload = result.structuredContent
    if not isinstance(payload, dict):
        return {"is_error": result.isError, "content": str(result.content)}
    nested = payload.get("result")
    return nested if isinstance(nested, dict) else payload


async def _run(url: str, token: str, command: str) -> None:
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
    asyncio.run(_run(args.url, args.token, args.command))


if __name__ == "__main__":
    main()
