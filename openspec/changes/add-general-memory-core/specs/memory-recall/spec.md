## ADDED Requirements

### Requirement: Scope every recall to the current user
Every memory recall operation MUST require trusted current-user context and MUST restrict candidate retrieval to that user before semantic or model-based relevance processing occurs. A conversation identifier alone MUST NOT establish memory ownership.

#### Scenario: Two users study the same company
- **WHEN** user B starts a task about a company also studied by user A
- **THEN** no private memory owned by user A enters user B’s candidate set, answer context, usage record, or source display

### Requirement: Recall only eligible current memories
Automatic recall MUST exclude pending-confirmation, superseded, expired, revoked, deleted, and sensitive-blocked content. Historical content MAY be returned only when the user explicitly requests history and it MUST be labeled as historical.

#### Scenario: Current and superseded hypotheses both match
- **WHEN** a new task is semantically related to both a current hypothesis and its superseded predecessor
- **THEN** automatic current-context recall includes the current hypothesis only

#### Scenario: User asks for hypothesis evolution
- **WHEN** the user explicitly asks how a hypothesis changed over time
- **THEN** the system may return current and historical versions
- **AND** each version is labeled with validity and source context

### Requirement: Use task context to select a bounded relevant set
Recall SHALL consider the current user, scenario, subject, task intent, memory type, validity, and semantic relevance. The system MUST apply a configurable upper bound and relevance threshold so that an unrelated or weakly related memory is not injected merely because a fixed result count is available.

#### Scenario: Relevant memories exist
- **WHEN** a user asks what to review for a known subject and several active memories are directly relevant
- **THEN** the system supplies a bounded set covering the most relevant current items

#### Scenario: No memory is sufficiently relevant
- **WHEN** all scoped active memories are unrelated to the current task
- **THEN** the system proceeds without memory context
- **AND** it does not fill the result set with irrelevant memories

### Requirement: Respect current instructions over remembered preferences
Explicit instructions in the current task MUST override conflicting historical preferences for that task without automatically changing the stored preference.

#### Scenario: Current output request conflicts with a preference
- **WHEN** a remembered preference asks for detailed output but the current task explicitly asks for a short answer
- **THEN** the answer follows the current short-answer instruction
- **AND** the stored long-term preference remains unchanged unless the user explicitly updates it

### Requirement: Preserve epistemic labels during use
Recalled user views, externally sourced facts, and system inferences MUST remain distinguishable in answer context and presentation. A recalled user view MUST NOT be presented as an independently verified fact.

#### Scenario: Recalling a user hypothesis
- **WHEN** a user hypothesis is relevant to the current task
- **THEN** the answer attributes it to the user’s prior research context
- **AND** it does not claim that the hypothesis has been externally verified

### Requirement: Explain and record memory use
For every answer that uses long-term memory, the system SHALL record the memory versions supplied and actually used, the current user, the task or turn, and the use time. The user MUST be able to inspect which memories influenced the answer and trace them to source expressions.

#### Scenario: Answer uses two memories
- **WHEN** an answer relies on two recalled memories
- **THEN** the response exposes both memories and their sources
- **AND** a usage record associates the answer turn with the exact memory versions

### Requirement: Fail safely when memory is unavailable
A failure in optional memory recall MUST NOT cause the system to use unscoped, stale, or partially retrieved memory. The system MAY continue without long-term memory when the primary task can still be answered safely.

#### Scenario: Memory index is unavailable
- **WHEN** the memory relevance index cannot be queried
- **THEN** the system either uses a safe structured fallback within the same user scope or continues without memory
- **AND** it does not broaden the user scope or reuse cached context from another user
