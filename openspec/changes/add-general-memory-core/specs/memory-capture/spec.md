## ADDED Requirements

### Requirement: Discover candidates after completed work
The system SHALL inspect each completed conversation turn or explicitly completed stage for information with plausible value beyond the current task. Candidate discovery MUST cover durable preferences, stable user context, and ongoing matters defined by the active scenario, without requiring the user to say “remember this”.

#### Scenario: One turn contains multiple durable items
- **WHEN** a user clearly states a durable preference, an ongoing judgment, and a future validation condition in one completed turn
- **THEN** the system produces separate atomic candidates for each item
- **AND** every candidate refers to the source turn

#### Scenario: A turn contains no durable information
- **WHEN** a completed turn contains only casual conversation or a one-time formatting instruction
- **THEN** the system does not create an active long-term memory from that content

### Requirement: Preserve attribution and meaning
Every candidate MUST identify its owning user, source conversation, source expression, scenario, subject, memory type, content nature, observation time, save rationale, and extraction confidence when those values are available. The system MUST distinguish user views, user-provided facts, externally sourced facts, and system inferences.

#### Scenario: User expresses an investment view
- **WHEN** a user states a personal investment hypothesis about a company
- **THEN** the candidate is attributed to that user as a view
- **AND** it is not represented as an independently verified company fact

#### Scenario: Relative time is used
- **WHEN** a candidate contains an expression such as “next quarter” or “the third-quarter report”
- **THEN** the system preserves the original time expression and its source-time context
- **AND** any normalized interpretation remains distinguishable from the original wording

### Requirement: Decide admission conservatively
The system SHALL assign every candidate exactly one admission decision: automatic save, pending confirmation, discard, or sensitive block. Clear user statements with durable value MAY be automatically saved; ambiguous statements, weak inferences, and unclear conflicts MUST remain pending and MUST NOT be used as current memory before confirmation.

#### Scenario: Clear durable user statement
- **WHEN** a user clearly states an ongoing preference or matter with future use
- **THEN** the system may save it as an active memory
- **AND** the save decision and rationale are available for review

#### Scenario: Ambiguous inferred preference
- **WHEN** the system can only weakly infer a durable preference from the user’s behavior
- **THEN** it marks the candidate as pending confirmation
- **AND** subsequent answers do not use the candidate as current memory

#### Scenario: Temporary instruction
- **WHEN** the user says that only the current answer should be shorter
- **THEN** the system discards that candidate as temporary

### Requirement: Give explicit user statements priority
The system MUST treat explicit user statements and manual corrections as stronger evidence of user intent than system inference. A system inference MUST NOT independently create or replace a formal user judgment.

#### Scenario: Model infers a changed judgment from new data
- **WHEN** new data appears inconsistent with an active user judgment but the user does not explicitly revise that judgment
- **THEN** the system may propose a pending update
- **AND** it keeps the user’s current judgment unchanged until confirmation

### Requirement: Block prohibited memory persistence
The system MUST prevent configured prohibited content, including credentials, account secrets, real holdings, and transaction instructions, from becoming long-term memory. A blocked candidate MUST NOT retain the prohibited raw content in the long-term memory store or semantic index.

#### Scenario: User includes an account password
- **WHEN** a completed turn contains a password together with otherwise memorable context
- **THEN** the password is not persisted as candidate content, memory content, evidence text, or an embedding
- **AND** the system may retain a non-content audit outcome that a sensitive block occurred

### Requirement: Process source turns idempotently
Reprocessing the same source turn with the same capture policy MUST NOT create duplicate active memories or duplicate pending-review items. The system SHALL expose whether capture completed, failed, or requires reprocessing.

#### Scenario: Capture is retried after an interruption
- **WHEN** the same source turn is processed again after an uncertain failure
- **THEN** the resulting active and pending memories are equivalent to one successful processing attempt
- **AND** source evidence is not duplicated
