"""Memory Core 对外依赖的端口。"""

from memory_mcp.core.ports.capture import (
    CandidateExtractor,
    ExtractionRequest,
    SensitiveContentGuard,
    SensitiveInspection,
)
from memory_mcp.core.ports.profiles import (
    MemoryMetadataPolicy,
    MemoryProfile,
    ProfileRegistry,
)
from memory_mcp.core.ports.repositories import (
    CaptureWrite,
    DuplicateEvidenceWrite,
    MemoryRepository,
    ReplacementWrite,
)

__all__ = [
    "CandidateExtractor",
    "CaptureWrite",
    "DuplicateEvidenceWrite",
    "ExtractionRequest",
    "MemoryMetadataPolicy",
    "MemoryProfile",
    "MemoryRepository",
    "ProfileRegistry",
    "ReplacementWrite",
    "SensitiveContentGuard",
    "SensitiveInspection",
]
