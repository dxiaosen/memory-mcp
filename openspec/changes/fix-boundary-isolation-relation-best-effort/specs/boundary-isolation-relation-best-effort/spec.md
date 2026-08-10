## ADDED Requirements

### Requirement: Relation extraction is best-effort and does not roll back candidates

Relation extraction is a derived enhancement; it SHALL NOT participate in the Capture main-chain atomic boundary. When the relation planner exhausts retries (fatal validation failures, model structured-output errors), it SHALL return an empty plan and log `memory.capture.relation_extraction_failed` with `candidate_persistence_preserved=true`, rather than raising `InvalidModelOutputError`. When the repository `commit_capture` fails because of a relation write error, the CaptureService SHALL retry the commit with an empty relations tuple and log `memory.capture.relation_commit_failed` with `candidate_persistence_preserved=true`. In both cases the capture SHALL complete and persist candidate memories/reviews/replacements; `relation_accepted_count` SHALL be 0.

#### Scenario: Fatal validation exhausted does not fail capture

- **WHEN** every relation extraction attempt produces a fatal rejection (invalid source_expression)
- **THEN** the capture completes, candidate memories are persisted, `relation_accepted_count` is 0, and no `memory.capture.incomplete` is written

#### Scenario: Relation write failure retries without relations

- **WHEN** `commit_capture` raises because a relation endpoint is unavailable
- **THEN** the CaptureService retries the commit with an empty relations tuple, logs `relation_commit_failed`, and the capture completes with candidates persisted

### Requirement: Single candidate field errors are candidate-level discards

When `validate_memory_type` or `validate_business_progress` raises for a single candidate, the processor SHALL discard that candidate with reason_code `invalid_memory_type` / `invalid_business_progress` / `invalid_candidate_field` and continue processing the remaining candidates. The capture SHALL NOT fail. Only batch-level structural failures (None output, unparseable CandidateBatch, exhausted structured-output retry) may fail the capture.

#### Scenario: Invalid memory_type discards one candidate

- **WHEN** a candidate batch contains one valid candidate and one with an unregistered memory_type
- **THEN** the valid candidate is persisted, the invalid one is discarded with `invalid_memory_type`, and the capture completes

### Requirement: revoke_memory cascades active relations with valid stale_at

When `revoke_memory` revokes a memory that is an endpoint of active revision-scoped relations, the repository SHALL mark those relations `stale` with `stale_at` set to the revoke moment (now), so that `stale_at >= relation.created_at` holds and the `memory_relations_terminal_state` CHECK is satisfied. The CheckViolation SHALL NOT be surfaced to the MCP client. Revoking a memory with no active relations succeeds normally.

#### Scenario: Revoke source endpoint cascades stale

- **WHEN** an active memory has one active relation and the user revokes it
- **THEN** the relation becomes `stale` (visible via `include_inactive=True`), the memory is `revoked`, and no CheckViolation is raised

### Requirement: Mutation tools declare explicit-management-only boundary

The `revoke_memory`, `link_memories`, `revoke_memory_relation`, `confirm_pending_memory`, `batch_confirm_pending`, and `reject_pending_memory` tool descriptions SHALL state that they are to be used only when the user explicitly asks to inspect or manage stored Memory MCP records, and that normal business semantic updates (changing/correcting a judgment, stating support/challenge/threat) are handled by the AfterRun capture lifecycle--not by proactive mutation calls.

#### Scenario: revoke_memory description states the boundary

- **WHEN** the `revoke_memory` tool is registered
- **THEN** its description instructs the agent to use it only for explicit record management, not for ordinary business-judgment changes
