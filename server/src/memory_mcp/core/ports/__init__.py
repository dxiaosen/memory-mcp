"""Memory Core 对外依赖的端口。"""

from memory_mcp.core.ports.capture import (
    MAX_RELATION_ENDPOINTS,
    MAX_RELATION_PROPOSALS,
    CandidateExtractor,
    ExtractionRequest,
    RelationExtractionRequest,
    RelationExtractor,
    SensitiveContentGuard,
    SensitiveInspection,
)
from memory_mcp.core.ports.embedding import EmbeddingProvider, embed_single
from memory_mcp.core.ports.profiles import (
    MemoryExpiryDerivation,
    MemoryMetadataPolicy,
    MemoryProfile,
    MemoryRelationPolicy,
    ProfileRegistry,
    profile_fingerprint,
)
from memory_mcp.core.ports.repositories import (
    CaptureEnqueueWrite,
    CaptureWrite,
    DuplicateEvidenceWrite,
    MemoryRepository,
    PendingCapture,
    RecallCandidateSet,
    ReplacementWrite,
)

__all__ = [
    "MAX_RELATION_ENDPOINTS",
    "MAX_RELATION_PROPOSALS",
    "CandidateExtractor",
    "CaptureEnqueueWrite",
    "CaptureWrite",
    "DuplicateEvidenceWrite",
    "EmbeddingProvider",
    "ExtractionRequest",
    "MemoryExpiryDerivation",
    "MemoryMetadataPolicy",
    "MemoryProfile",
    "MemoryRelationPolicy",
    "MemoryRepository",
    "PendingCapture",
    "ProfileRegistry",
    "RecallCandidateSet",
    "RelationExtractionRequest",
    "RelationExtractor",
    "ReplacementWrite",
    "SensitiveContentGuard",
    "SensitiveInspection",
    "embed_single",
    "profile_fingerprint",
]
