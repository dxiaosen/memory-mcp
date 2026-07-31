"""完成轮次捕获 MCP 工具。"""

import asyncio
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from memory_mcp.auth import MemoryScope
from memory_mcp.errors import UnsupportedContractVersionError
from memory_mcp.schemas import (
    CaptureReceipt,
    CompletedTurnEventV1,
    ErrorResponse,
    RoleMessageV1,
)
from memory_mcp.tools.shared import ToolSupport, request_id


class CaptureTools(ToolSupport):
    def _register_capture(self, server: FastMCP[Any]) -> None:
        @server.tool(
            name="capture_completed_turn",
            description=(
                "Capture one successfully completed Agent turn. "
                "Owner identity is derived from the access token."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def capture_completed_turn(
            event_id: str,
            contract_version: str,
            conversation_id: str,
            turn_id: str,
            observed_at: datetime,
            messages: list[RoleMessageV1],
            ctx: Context,
            profile_id: str = "general-work",
            subject_hint: str | None = None,
        ) -> CaptureReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.WRITE)
                event = CompletedTurnEventV1.model_validate(
                    {
                        "event_id": event_id,
                        "contract_version": contract_version,
                        "profile_id": profile_id,
                        "conversation_id": conversation_id,
                        "turn_id": turn_id,
                        "observed_at": observed_at,
                        "messages": messages,
                        "subject_hint": subject_hint,
                    }
                )
                if event.contract_version != "1":
                    raise UnsupportedContractVersionError(event.contract_version)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "capture_completed_turn",
                    event_id=event.event_id,
                )
                result = await asyncio.to_thread(
                    self._service.capture_turn,
                    principal.to_core(),
                    event.to_turn_envelope(
                        max_characters=self._settings.max_capture_characters
                    ),
                )
                receipt = CaptureReceipt.from_result(
                    current_request_id,
                    result,
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "capture_completed_turn",
                    started_at,
                    status=result.status.value,
                    result_count=len(result.outcomes),
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "capture_completed_turn",
                    exc,
                )
