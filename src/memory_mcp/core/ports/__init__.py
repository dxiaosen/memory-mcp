"""Memory Core 对外依赖的端口。"""

from memory_mcp.core.ports.capture import (
    CandidateExtractor,
    ExtractionRequest,
    SensitiveContentGuard,
    SensitiveInspection,
)
from memory_mcp.core.ports.repositories import (
    CaptureWrite,
    DuplicateEvidenceWrite,
    MemoryRepository,
    ReplacementWrite,
)
from memory_mcp.core.ports.scenarios import ScenarioPolicy, ScenarioRegistry

__all__ = [
    "CandidateExtractor",
    "CaptureWrite",
    "DuplicateEvidenceWrite",
    "ExtractionRequest",
    "MemoryRepository",
    "ReplacementWrite",
    "ScenarioPolicy",
    "ScenarioRegistry",
    "SensitiveContentGuard",
    "SensitiveInspection",
]
