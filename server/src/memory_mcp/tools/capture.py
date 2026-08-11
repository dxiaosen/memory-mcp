"""完成轮次捕获 MCP 工具。"""

import asyncio
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from memory_mcp.auth import MemoryScope
from memory_mcp.errors import UnsupportedContractVersionError
from memory_mcp.schemas import (
    CaptureReceipt,
    CompletedTurnInputV1,
    ErrorResponse,
)
from memory_mcp.tools.shared import ToolSupport, request_id


class CaptureTools(ToolSupport):
    def _register_capture(self, server: FastMCP[Any]) -> None:
        @server.tool(
            name="capture_completed_turn",
            description=(
                "Capture one successfully completed Agent turn into long-term "
                "memory. Call this ONLY after a turn where the user stated or "
                "revised a durable fact, preference, decision, thesis, or "
                "research judgment worth remembering across future sessions. "
                "Do NOT call this for turns that only inspect, query, search, "
                "or manage existing memories, or for casual/operational "
                "conversation with no lasting signal. Owner identity is "
                "derived from the access token; conversation_id and turn_id "
                "must be stable across the same conversation so deduplication "
                "and replay work correctly."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def capture_completed_turn(
            conversation_id: str,
            turn_id: str,
            user_input: str,
            final_output: str,
            ctx: Context,
            profile_id: str | None = None,
            subject_hint: str | None = None,
        ) -> CaptureReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.WRITE)
                resolved_profile_id = profile_id or principal.default_profile_id
                event = CompletedTurnInputV1(
                    profile_id=resolved_profile_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    user_input=user_input,
                    final_output=final_output,
                    subject_hint=subject_hint,
                )
                envelope = event.to_turn_envelope(
                    owner_id=principal.owner_key,
                    max_characters=self._settings.max_capture_characters,
                    clock=self._service.clock,
                )
                if envelope.contract_version != "1":
                    raise UnsupportedContractVersionError(envelope.contract_version)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "capture_completed_turn",
                    event_id=envelope.event_id,
                )
                result = await asyncio.to_thread(
                    self._service.capture_turn,
                    principal.to_core(),
                    envelope,
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
