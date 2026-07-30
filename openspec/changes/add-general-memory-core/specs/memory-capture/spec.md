## ADDED Requirements

### Requirement: Accept completed turns through MCP
The system SHALL expose a versioned `capture_completed_turn` MCP tool for Agent
AfterRun Hooks. The tool MUST accept an explicit contract version, stable event
identifier, scenario, conversation, turn, observation time, and role-labeled message
blocks. The tool MUST NOT accept a memory owner identifier.

#### Scenario: Agent Hook submits a successful turn
- **WHEN** an Agent produces a final result and its AfterRun Hook submits the completed turn
- **THEN** the MCP Server processes that turn under the authenticated current-user scope
- **AND** it returns a structured capture receipt

#### Scenario: An incomplete run is not captured
- **WHEN** an Agent run is cancelled or fails before producing a final result
- **THEN** the default Hook does not submit the turn for automatic long-term capture

#### Scenario: A top-level user run completes
- **WHEN** one top-level user task produces its final Agent response
- **THEN** the AfterRun Hook submits exactly one completed-turn event for that run
- **AND** it does not wait for the broader conversation to be closed

#### Scenario: An Agent executes internal steps
- **WHEN** the Agent invokes tools, child runs, or internal retries before its final response
- **THEN** those internal steps do not independently trigger the default AfterRun capture

#### Scenario: Hook uses an unsupported event version
- **WHEN** a Hook submits a completed-turn contract major version the server does not support
- **THEN** the server returns `unsupported_contract_version`
- **AND** it does not guess field meaning or create capture state

### Requirement: Preserve role and source attribution
The system MUST distinguish user, assistant, and tool message blocks. A user message MAY
provide evidence for an automatically saved user view, preference, context, or ongoing
matter. Assistant or tool content MUST NOT be automatically represented as an explicit
user view.

#### Scenario: User and assistant statements differ
- **WHEN** a user states a preference and the assistant proposes a different preference
- **THEN** only the user statement can be automatically attributed to the user
- **AND** the assistant proposal is discarded or remains pending

#### Scenario: Tool output suggests a new fact
- **WHEN** a tool result contains information not explicitly adopted by the user
- **THEN** the system labels it as external context or system inference
- **AND** it does not automatically replace the user’s current memory

### Requirement: Discover atomic durable candidates
The system SHALL inspect each submitted completed turn for atomic information with
plausible value beyond the current task. Candidate discovery MUST use the active
ScenarioPolicy and MUST NOT require the user to say “remember this”.

#### Scenario: One turn contains multiple durable items
- **WHEN** a user clearly states a stable project convention, a durable preference, and an ongoing item in one turn
- **THEN** the system produces a separate atomic candidate for each item
- **AND** every candidate refers to the submitted event and source message

#### Scenario: A turn contains no durable information
- **WHEN** a submitted turn contains only casual conversation or a one-time formatting instruction
- **THEN** the system does not create active long-term memory from that content

### Requirement: Provide configured structured extraction
The runnable MCP Server SHALL construct a CandidateExtractor from protected runtime
configuration. It MUST support one real OpenAI-compatible structured model backend and
one deterministic fixed backend implementing the same CandidateExtractor contract.
Backend selection MUST NOT change trusted identity, provenance, admission, lifecycle,
or persistence rules.

#### Scenario: Real structured extraction is configured
- **WHEN** the server starts with a supported model provider, model, endpoint, and credential
- **THEN** completed turns are submitted to the real structured backend after redaction
- **AND** validated candidates continue through the common admission pipeline

#### Scenario: Fixed extraction is selected
- **WHEN** tests, offline demonstration, or recovery configuration selects the fixed backend
- **THEN** the server produces deterministic configured candidates without external network access
- **AND** the MCP and Hook contracts remain identical to the real-backend path

#### Scenario: Model configuration is incomplete
- **WHEN** the selected real backend lacks required model credentials or settings
- **THEN** server startup fails with a non-content configuration error
- **AND** it does not silently change identity, owner scope, or storage behavior

### Requirement: Give every candidate exactly one admission result
The system SHALL assign every candidate exactly one of `auto_save`, `pending`, `discard`,
or `blocked`. Ambiguous statements, system inferences, unclear conflicts, and uncertain
replacement MUST remain pending and MUST NOT be recalled before confirmation.

#### Scenario: Clear durable user statement
- **WHEN** a user clearly states a durable preference or ongoing matter allowed by the active scenario
- **THEN** the system may save it as active memory
- **AND** the capture receipt reports an auto-save outcome

#### Scenario: Ambiguous inferred preference
- **WHEN** a preference can only be weakly inferred from assistant or tool context
- **THEN** the system creates a pending review item
- **AND** subsequent recall does not use it

#### Scenario: Temporary instruction
- **WHEN** the user requests a format change only for the current answer
- **THEN** the system reports a discard outcome
- **AND** it does not persist the temporary instruction as active memory

### Requirement: Derive trusted identity and source values server-side
The MCP Server MUST construct owner scope and client identity from authenticated
request context. It MUST derive conversation, source turn, and observation time from
the validated completed-turn event. Model output MUST NOT override either identity or
event provenance, and the capture payload MUST NOT contain an owner selector.

#### Scenario: Payload attempts to specify another owner
- **WHEN** a client includes an owner-like field in a capture request
- **THEN** schema validation rejects the request
- **AND** no capture state is written

#### Scenario: Model emits a different owner or source
- **WHEN** structured extraction output contains identity or source values different from the trusted request
- **THEN** the system ignores the model-provided values
- **AND** uses authenticated identity and validated submitted-event metadata

### Requirement: Block prohibited content before persistence
The system MUST redact configured prohibited content before candidate extraction and
MUST inspect every free-text candidate field that can be persisted, including subject,
content, source expression, save rationale, business progress, and original time
expression, source message identifier, and source tool name. Blocked raw content MUST
NOT be stored in memory content, evidence, review items, semantic representations, tool
results, exception messages, or logs.

#### Scenario: Completed turn includes a fictional credential
- **WHEN** a submitted turn contains a credential together with otherwise durable context
- **THEN** the credential is redacted before model processing
- **AND** the capture receipt contains only a non-content blocked category

#### Scenario: Extractor returns prohibited content
- **WHEN** structured model output contains prohibited content
- **THEN** the candidate is blocked before persistence
- **AND** no prohibited raw text appears in the MCP response

#### Scenario: Extractor hides prohibited content outside the content field
- **WHEN** structured model output places prohibited text in a subject, rationale, or time-expression field
- **THEN** the whole candidate is blocked before persistence
- **AND** neither storage nor operational logs contain the prohibited text

### Requirement: Process completed-turn events idempotently
Reprocessing the same owner, scenario, event identifier, and policy version with the
same payload MUST return the original logical result without creating duplicate
memories, evidence, or pending items. Reusing the event identifier with a different
payload MUST fail with `idempotency_conflict`.

#### Scenario: Hook retries after a timeout
- **WHEN** an AfterRun Hook retries the same completed event after an uncertain response
- **THEN** the system returns an equivalent capture receipt marked as replayed
- **AND** no duplicate state is created

#### Scenario: Two retries overlap in time
- **WHEN** two requests for the same owner, scenario, event, policy version, and payload overlap
- **THEN** at most one request performs candidate extraction and commits the logical result
- **AND** both callers observe the committed result rather than a database-constraint error

#### Scenario: Event identifier is reused for different content
- **WHEN** a client submits different message content under an already completed event identifier
- **THEN** the system rejects the request with `idempotency_conflict`
- **AND** preserves the original completed result

#### Scenario: Hook run key is reused with a different payload
- **WHEN** one Hook process receives different user or final-output content under an already observed top-level run key
- **THEN** the Hook rejects the conflicting local invocation instead of returning the first payload's cached result
- **AND** it does not submit a second ambiguous event

### Requirement: Run AfterRun asynchronously without requiring a queue
The Hook SDK SHALL expose AfterRun as an asynchronous operation so network I/O does not
block the Agent event loop. The default Runner MUST await the structured capture receipt
before declaring its wrapped run complete. The single-process MVP MUST NOT require an
external message queue; a Host MAY schedule the coroutine after emitting the final user
response only if it explicitly accepts process-crash loss and still preserves the stable
event identifier.

#### Scenario: Default Runner captures a successful result
- **WHEN** the Agent callable returns its final output
- **THEN** the Runner asynchronously calls `capture_completed_turn`
- **AND** returns only after it has a capture receipt or a configured fail-open warning

#### Scenario: Host emits the response before capture finishes
- **WHEN** a Host chooses to schedule AfterRun in its own background task
- **THEN** the Hook uses the same stable event identifier and bounded retry behavior
- **AND** documentation states that an in-process background task is not a durable queue

### Requirement: Return a structured capture receipt
The capture tool SHALL return capture status, replay state, policy version, decision
counts, created memory identifiers, pending review identifiers, and a stable failure
code when applicable. Discarded and blocked outcomes MUST NOT expose candidate content.

#### Scenario: Mixed capture result
- **WHEN** one turn produces auto-saved, pending, discarded, and blocked outcomes
- **THEN** the response exposes the four counts and permitted opaque identifiers
- **AND** it does not expose discarded or blocked raw text

### Requirement: Commit capture in the authoritative database
The deployed service MUST commit the capture run, decision outcomes, active memories,
evidence, and pending items in one PostgreSQL transaction. A process restart or network
retry MUST NOT weaken event idempotency. SQLite MAY remain only as historical prototype
evidence while PostgreSQL is the deployed authority.

#### Scenario: Server restarts after a completed capture
- **WHEN** the MCP process restarts after PostgreSQL committed a completed event
- **THEN** a replay returns the original logical receipt
- **AND** the extractor is not invoked to create duplicate state

#### Scenario: Capture transaction fails before commit
- **WHEN** persistence fails while any part of a capture transaction is being written
- **THEN** PostgreSQL exposes none of that transaction's partial memories or review items
- **AND** a later retry can safely process the same event identifier
