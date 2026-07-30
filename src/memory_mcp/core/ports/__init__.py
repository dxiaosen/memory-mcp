"""Memory Core 对外依赖的端口。"""

from memory_mcp.core.ports.capture import (
    CandidateExtractor,
    ExtractionRequest,
    SensitiveContentGuard,
    SensitiveInspection,
)
from memory_mcp.core.ports.repositories import CaptureWrite, MemoryRepository
from memory_mcp.core.ports.scenarios import ScenarioPolicy, ScenarioRegistry

__all__ = [
    "CandidateExtractor",
    "CaptureWrite",
    "ExtractionRequest",
    "MemoryRepository",
    "ScenarioPolicy",
    "ScenarioRegistry",
    "SensitiveContentGuard",
    "SensitiveInspection",
]
