## ADDED Requirements

### Requirement: Enforce user isolation across all memory operations
Every capture, save, match, recall, view, update, history, usage, revoke, and delete operation MUST be scoped by trusted user context at the application and storage boundaries. The system MUST NOT rely on a model prompt to enforce isolation.

#### Scenario: Cross-user identifier is supplied
- **WHEN** a user attempts to read or modify a memory identifier owned by another user
- **THEN** the operation reveals no memory content or ownership details
- **AND** no state is changed

#### Scenario: Model emits a different user identifier
- **WHEN** model output contains a user identifier different from the trusted request context
- **THEN** the system ignores the model-provided identity
- **AND** all memory operations remain scoped to the trusted current user

### Requirement: Keep pending content under user control
Pending-confirmation content MUST remain separate from active memory. A user SHALL be able to inspect the proposed content, source, reason, and proposed relationship before confirming or rejecting it.

#### Scenario: User confirms a pending memory
- **WHEN** a user confirms a pending candidate
- **THEN** it becomes eligible active memory within that user’s scope
- **AND** the confirmation time and action are recorded

#### Scenario: User rejects a pending replacement
- **WHEN** a user rejects a proposed replacement
- **THEN** the current active memory remains unchanged
- **AND** the rejected proposal is not used in later answers

### Requirement: Provide memory management operations
A user SHALL be able to list current memories, inspect source and history, correct content, revoke current use, request deletion, review pending items, and inspect answer usage for memories they own.

#### Scenario: User corrects an active memory
- **WHEN** a user corrects the content of an active memory
- **THEN** the correction becomes the current version
- **AND** the prior version is excluded from automatic current recall

#### Scenario: User revokes a memory
- **WHEN** a user revokes an active memory
- **THEN** it is immediately excluded from automatic recall
- **AND** it remains identifiable as revoked history until deleted

### Requirement: Honor deletion without retaining memory content
After an owned-memory deletion request is completed, the deleted content, source excerpts, and derived semantic representation MUST NOT appear in current views, history views, search results, recall context, or answer usage. The system MAY retain a minimal non-content audit marker containing only the deletion action, time, and opaque identifier.

#### Scenario: User deletes a memory with historical versions
- **WHEN** a user deletes a memory that has prior versions and source excerpts
- **THEN** all content-bearing versions, excerpts, and derived index entries are removed from memory access paths
- **AND** later recall cannot reconstruct the deleted content from the memory subsystem

#### Scenario: A deleted source is processed again
- **WHEN** an old source turn that produced a deleted memory is reprocessed without a new explicit user statement
- **THEN** the system does not automatically recreate the deleted memory
- **AND** the suppression mechanism does not retain reconstructable deleted content

### Requirement: Make significant actions auditable
The system SHALL record non-sensitive audit events for automatic saves, confirmations, corrections, replacements, expirations, revocations, deletions, sensitive blocks, and memory use. Audit access MUST follow the same user isolation rules as memory access.

#### Scenario: An automatic replacement occurs
- **WHEN** an explicit user statement replaces an active memory
- **THEN** the audit history records the triggering source, old and new opaque memory identifiers, action, actor class, and time
- **AND** the event does not expose content to another user

### Requirement: Distinguish prototype isolation from authentication
The prototype MUST demonstrate complete logical isolation for at least three virtual users, but it MUST NOT present virtual-user selection as production identity authentication or authorization.

#### Scenario: Demo switches virtual users
- **WHEN** the demonstration changes from one virtual user to another
- **THEN** each user sees and uses only their own memories
- **AND** the interface or documentation identifies the selector as a prototype identity mechanism

### Requirement: Expose the sensitive-processing boundary
The system MUST state that sensitive memory blocking prevents prohibited long-term persistence but does not, by itself, prove that content was redacted before every model call. The prototype MUST use only fictional or publicly sanitized cases.

#### Scenario: User reviews the sensitive policy
- **WHEN** a user or reviewer inspects the prototype’s memory policy
- **THEN** the persistence boundary and model-processing limitation are visible
- **AND** the system does not claim production-grade data-loss prevention
