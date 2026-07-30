## ADDED Requirements

### Requirement: Recall memory through an MCP BeforeRun Hook
The system SHALL expose a `recall_memory` MCP tool intended for deterministic use by
Agent BeforeRun Hooks. The Hook MUST call recall programmatically before the Agent
model receives the current task; recall MUST NOT depend solely on the model choosing
to call a memory tool.

#### Scenario: Agent begins a new task
- **WHEN** an authenticated Agent Hook submits the current scenario, query, and optional subject
- **THEN** the MCP Server returns a structured current-memory context before model execution

#### Scenario: Host lacks a native Hook API
- **WHEN** an Agent Host cannot run a native BeforeRun callback
- **THEN** an outer Runner MAY perform the same MCP call before invoking the Agent
- **AND** the server-side recall contract remains unchanged

### Requirement: Scope recall by authenticated owner before ranking
Every recall operation MUST derive owner scope from authenticated request context and
MUST restrict the candidate set before subject matching, text scoring, or model-based
processing. A conversation identifier, Agent identifier, or tool argument MUST NOT
establish ownership.

#### Scenario: Same user uses two Agent clients
- **WHEN** user A creates memory through Agent A and later queries through Agent B
- **THEN** Agent B can recall user A’s eligible memory
- **AND** the usage of a different client identifier does not create a different owner scope

#### Scenario: Different user uses the same Agent
- **WHEN** user B asks the same question through Agent B
- **THEN** no memory owned by user A enters the candidate set or result

### Requirement: Recall only current eligible memory
Automatic recall MUST exclude pending, superseded, expired, revoked, deleted, and
sensitive-blocked content. Historical content MAY be returned only through explicit
history inspection and MUST NOT be included in the rendered Agent context. Lifecycle
states that are not actively transitioned by the MVP MUST still remain safely excluded
if present in existing or future data.

#### Scenario: Active and superseded memories both match
- **WHEN** both an active memory and its superseded predecessor are textually relevant
- **THEN** `recall_memory` returns the active memory only

#### Scenario: Pending proposal matches strongly
- **WHEN** a pending proposal closely matches the current query
- **THEN** it remains absent from automatic recall until confirmation

### Requirement: Return a bounded relevant set
Recall SHALL consider scenario, subject, task intent, memory type priority, validity,
and text relevance. The server MUST enforce a relevance threshold, maximum item count,
and token budget. It MUST return an empty set rather than fill the result with unrelated
memory.

#### Scenario: Relevant current memories exist
- **WHEN** a task names a known subject with several directly relevant active memories
- **THEN** the system returns a bounded set ordered by configured relevance

#### Scenario: No memory is relevant
- **WHEN** all scoped active memories are unrelated to the task
- **THEN** the system returns no memory items
- **AND** the Agent proceeds without long-term memory context

### Requirement: Preserve epistemic labels and source summaries
Every recalled item MUST include its exact revision identifier, memory type, subject,
assertion kind, observation time, and a source summary. A recalled user view MUST remain
distinguishable from verified fact and system inference.

#### Scenario: Recalled user decision
- **WHEN** a current user decision is supplied to an Agent
- **THEN** the result labels it as the user’s prior decision
- **AND** does not present it as independently verified fact

### Requirement: Provide safe rendered context
The recall response SHALL include structured items and a server-rendered context block.
The rendered block MUST state that recalled memory is historical user context rather
than a new instruction, and that the current user request has priority.

#### Scenario: Current task conflicts with a remembered format
- **WHEN** a remembered preference asks for detailed output but the current task requests a short answer
- **THEN** the Agent context instructs the model to follow the current request
- **AND** the stored preference is not silently changed

### Requirement: Fail safely when recall is unavailable
A recall failure MUST NOT broaden owner scope, reuse another user’s cache, or inject a
partial unvalidated result. A Hook MAY allow the primary Agent task to continue without
long-term memory.

#### Scenario: MCP Server cannot complete recall
- **WHEN** the Hook times out or the server reports temporary unavailability
- **THEN** the Agent may continue without memory
- **AND** no stale cross-user context is substituted

### Requirement: Keep PostgreSQL authoritative for recall
The deployed recall path MUST derive its eligible current set from PostgreSQL. If a
future text or vector index is introduced, it MAY propose candidates only; the service
MUST revalidate owner, current revision, and lifecycle eligibility against PostgreSQL
before returning or rendering any item.

#### Scenario: Optional index contains a stale revision
- **WHEN** an external index returns a superseded or differently owned revision
- **THEN** the service removes it during authoritative revalidation
- **AND** the stale content does not enter the structured result or rendered context
