"""通用记忆领域模型。"""

from memory_mcp.core.domain.capture import (
    AdmissionDecision,
    Candidate,
    CandidateDurability,
    CandidateProposal,
    CaptureOutcome,
    CaptureResult,
    CaptureStatus,
    ExpressionBasis,
    ExtractionMetadata,
    ReviewItem,
    ReviewStatus,
    TurnEnvelope,
    TurnMessage,
)
from memory_mcp.core.domain.lifecycle import (
    MemoryHistoryEntry,
    normalize_memory_text,
)
from memory_mcp.core.domain.models import (
    AssertionKind,
    Evidence,
    LifecycleStatus,
    MemoryItem,
    MemoryRecord,
    MemoryRevision,
    MessageRole,
    PrincipalContext,
)
from memory_mcp.core.domain.recall import (
    RecalledMemory,
    RecallQuery,
    RecallResult,
    RecallSourceSummary,
)

__all__ = [
    "AdmissionDecision",
    "AssertionKind",
    "Candidate",
    "CandidateDurability",
    "CandidateProposal",
    "CaptureOutcome",
    "CaptureResult",
    "CaptureStatus",
    "Evidence",
    "ExpressionBasis",
    "ExtractionMetadata",
    "LifecycleStatus",
    "MemoryHistoryEntry",
    "MemoryItem",
    "MemoryRecord",
    "MemoryRevision",
    "MessageRole",
    "PrincipalContext",
    "RecallQuery",
    "RecallResult",
    "RecallSourceSummary",
    "RecalledMemory",
    "ReviewItem",
    "ReviewStatus",
    "TurnEnvelope",
    "TurnMessage",
    "normalize_memory_text",
]
