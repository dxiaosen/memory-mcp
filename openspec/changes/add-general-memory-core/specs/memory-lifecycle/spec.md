## ADDED Requirements

### Requirement: Maintain one traceable current record
Every active memory MUST identify its owner scope, scenario, subject, memory type,
assertion kind, current content, formation time, save rationale, and at least one
traceable source. The MCP Server MUST preserve the same logical memory across calls
from different Agent clients acting for the same owner.

#### Scenario: A second Agent reads an existing memory
- **WHEN** Agent B acts for the same authenticated user who created memory through Agent A
- **THEN** the system exposes the same current logical memory
- **AND** its source still identifies the original Agent turn

### Requirement: Reinforce duplicate statements without creating copies
Within the same owner, scenario, subject, and compatible memory type, the system SHALL
treat equivalent durable content as duplicate reinforcement. It MUST retain one current
logical memory and attach the later source evidence.

#### Scenario: Equivalent preference is repeated through another Agent
- **WHEN** the same user repeats an equivalent preference through a different Agent client
- **THEN** the system keeps one active memory
- **AND** adds the later completed turn as supporting evidence

### Requirement: Apply explicit replacement consistently
An explicit user statement that an old memory is no longer current and names a
replacement MUST append a new revision to the same logical MemoryItem, make that
revision current and active, and preserve the former revision as non-current
superseded history. The old and new revision states MUST be committed atomically.

#### Scenario: User explicitly changes a durable preference
- **WHEN** the user says the former report format should no longer be used and gives a new default
- **THEN** the existing logical memory receives one new active current revision
- **AND** the former revision becomes non-current superseded history

#### Scenario: Replacement transaction fails
- **WHEN** persistence fails while applying an explicit replacement
- **THEN** the previous consistent current state remains in effect
- **AND** the system does not leave both versions current

### Requirement: Keep ambiguous conflicts pending
The system MUST NOT replace current memory when a conflict is inferred only from
assistant output, tool output, or ambiguous user language. It SHALL create a pending
review item or discard the proposal while leaving current memory unchanged.

#### Scenario: Agent infers a preference change
- **WHEN** an Agent concludes that a user probably changed preference without an explicit user statement
- **THEN** the current preference remains active
- **AND** the inferred change is not automatically recalled as current memory

### Requirement: Exclude historical content from normal recall
Superseded content MUST remain distinguishable from active content and MUST NOT enter
ordinary `recall_memory` results. Historical content MAY be returned only through an
explicit history request and MUST be labeled with status and source.

#### Scenario: Current and old preferences both match
- **WHEN** a task is related to both an active preference and its superseded predecessor
- **THEN** automatic recall returns only the active preference

#### Scenario: User explicitly inspects history
- **WHEN** the user requests a memory with `include_history=true`
- **THEN** the system may return current and superseded versions
- **AND** every version is labeled as current or historical

### Requirement: Keep scenario variation behind policy
The system SHALL allow ScenarioPolicy to define legal memory types, capture guidance,
policy version, optional business-progress values, optional relation declarations, and
recall priorities without changing common ownership, provenance, admission,
idempotency, or lifecycle semantics. A scenario that does not use progress or relations
MUST be able to declare empty values without removing those extension points.

#### Scenario: General-work policy is registered
- **WHEN** the service registers `preference`, `stable_context`, `ongoing_item`, and `decision` types
- **THEN** the common capture and recall flow accepts those types
- **AND** the Memory Core does not hard-code Agent-specific behavior

### Requirement: Preserve lifecycle invariants in PostgreSQL
The PostgreSQL repository MUST enforce registered scenario types, owner-consistent
references, one current revision per memory, and atomic review resolution independently
of application checks. Repository behavior MUST conform to the common domain contract
verified by the in-memory unit suite and PostgreSQL contract suite.

#### Scenario: Concurrent operations attempt two current revisions
- **WHEN** two transactions attempt to make different revisions current for the same memory
- **THEN** PostgreSQL commits at most one valid current state
- **AND** the other operation fails without exposing a split lifecycle
