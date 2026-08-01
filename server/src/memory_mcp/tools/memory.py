"""记忆列表与详情 MCP 工具。"""

import asyncio
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from memory_mcp.auth import MemoryScope
from memory_mcp.schemas import (
    ErrorResponse,
    MemoryDetailReceipt,
    MemoryListReceipt,
    MemoryRelationReceipt,
    MemoryRelationSummaryView,
    MemoryRelationView,
    MemoryRevisionView,
    MemoryRevocationReceipt,
    MemorySummaryView,
    MemoryView,
    decode_cursor,
    encode_cursor,
)
from memory_mcp.tools.shared import READ_ONLY, ToolSupport, request_id


class MemoryTools(ToolSupport):
    def _register_memory(self, server: FastMCP[Any]) -> None:
        @server.tool(
            name="list_memories",
            description="List current active memories for the authenticated owner.",
            annotations=READ_ONLY,
        )
        async def list_memories(
            ctx: Context,
            profile_id: str | None = None,
            subject: str | None = None,
            memory_type: str | None = None,
            limit: int = 50,
            cursor: str | None = None,
        ) -> MemoryListReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.READ)
                if not 1 <= limit <= 100:
                    raise ValueError("limit must be between 1 and 100")
                offset = decode_cursor(cursor)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "list_memories",
                )
                owner_records = await asyncio.to_thread(
                    self._service.list_memories,
                    principal.to_core(),
                )
                records = tuple(
                    record
                    for record in owner_records
                    if (profile_id is None or record.item.profile_id == profile_id)
                    and (subject is None or record.item.subject == subject)
                    and (memory_type is None or record.item.memory_type == memory_type)
                )
                selected = records[offset : offset + limit]
                next_offset = offset + len(selected)
                receipt = MemoryListReceipt(
                    request_id=current_request_id,
                    items=tuple(
                        MemorySummaryView.from_record(item) for item in selected
                    ),
                    next_cursor=(
                        encode_cursor(next_offset)
                        if next_offset < len(records)
                        else None
                    ),
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "list_memories",
                    started_at,
                    status="completed",
                    result_count=len(selected),
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "list_memories",
                    exc,
                )

        @server.tool(
            name="get_memory",
            description=(
                "Get one current memory and its sources. "
                "Cross-owner identifiers are indistinguishable from missing ones."
            ),
            annotations=READ_ONLY,
        )
        async def get_memory(
            memory_id: str,
            ctx: Context,
            include_history: bool = False,
        ) -> MemoryDetailReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.READ)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "get_memory",
                )
                identifier = UUID(memory_id)
                record = await asyncio.to_thread(
                    self._service.get_memory,
                    principal.to_core(),
                    identifier,
                )
                history = (
                    await asyncio.to_thread(
                        self._service.get_memory_history,
                        principal.to_core(),
                        identifier,
                    )
                    if include_history
                    else ()
                )
                relations = await asyncio.to_thread(
                    self._service.list_memory_relations,
                    principal.to_core(),
                    identifier,
                    include_inactive=include_history,
                )
                receipt = MemoryDetailReceipt(
                    request_id=current_request_id,
                    item=MemoryView.from_record(record),
                    history_included=include_history,
                    history=tuple(
                        MemoryRevisionView.from_entry(entry) for entry in history
                    ),
                    relations=tuple(
                        MemoryRelationSummaryView.from_summary(
                            summary,
                            include_provenance=include_history,
                        )
                        for summary in relations
                    ),
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "get_memory",
                    started_at,
                    status="completed",
                    result_count=1,
                    include_history=include_history,
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "get_memory",
                    exc,
                )

        @server.tool(
            name="revoke_memory",
            description=(
                "Revoke one owned current memory without deleting its traceable history."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def revoke_memory(
            memory_id: str,
            ctx: Context,
        ) -> MemoryRevocationReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.REVIEW)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "revoke_memory",
                )
                record = await asyncio.to_thread(
                    self._service.revoke_memory,
                    principal.to_core(),
                    UUID(memory_id),
                )
                receipt = MemoryRevocationReceipt(
                    request_id=current_request_id,
                    memory=MemoryView.from_record(record),
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "revoke_memory",
                    started_at,
                    status="revoked",
                    result_count=1,
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "revoke_memory",
                    exc,
                )

        @server.tool(
            name="link_memories",
            description=(
                "Create one directed relation between two owned active memories. "
                "The relation type and direction must be allowed by their profile."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def link_memories(
            source_memory_id: str,
            target_memory_id: str,
            relation_type: str,
            ctx: Context,
        ) -> MemoryRelationReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.WRITE)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "link_memories",
                )
                relation = await asyncio.to_thread(
                    self._service.link_memories,
                    principal.to_core(),
                    UUID(source_memory_id),
                    UUID(target_memory_id),
                    relation_type,
                )
                receipt = MemoryRelationReceipt(
                    request_id=current_request_id,
                    relation=MemoryRelationView.from_relation(relation),
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "link_memories",
                    started_at,
                    status=relation.status.value,
                    result_count=1,
                    relation_id=relation.relation_id,
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "link_memories",
                    exc,
                )

        @server.tool(
            name="revoke_memory_relation",
            description="Revoke one owned memory relation without deleting history.",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def revoke_memory_relation(
            relation_id: str,
            ctx: Context,
        ) -> MemoryRelationReceipt | ErrorResponse:
            current_request_id = request_id(ctx)
            try:
                principal = self._authorize(MemoryScope.REVIEW)
                started_at = self._log_started(
                    current_request_id,
                    principal,
                    "revoke_memory_relation",
                )
                relation = await asyncio.to_thread(
                    self._service.revoke_memory_relation,
                    principal.to_core(),
                    UUID(relation_id),
                )
                receipt = MemoryRelationReceipt(
                    request_id=current_request_id,
                    relation=MemoryRelationView.from_relation(relation),
                )
                self._log_completed(
                    current_request_id,
                    principal,
                    "revoke_memory_relation",
                    started_at,
                    status=relation.status.value,
                    result_count=1,
                    relation_id=relation.relation_id,
                )
                return receipt
            except Exception as exc:
                return self._error_response(
                    current_request_id,
                    "revoke_memory_relation",
                    exc,
                )
