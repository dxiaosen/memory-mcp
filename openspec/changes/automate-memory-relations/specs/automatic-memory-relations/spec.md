## ADDED Requirements

### Requirement: Relation extraction is Profile-driven and server-side
The Server SHALL run automatic relation extraction inside the authenticated Capture flow only for Profiles with non-empty `relation_policies`. Agent Hosts MUST NOT provide owner selectors, memory identifiers, relation settings, or a second model configuration. Generic Core code MUST NOT branch on investment-specific relation names.

#### Scenario: Investment capture has compatible endpoints
- **WHEN** an AfterRun turn uses `investment-research` and the trusted endpoint catalog contains a policy-compatible pair
- **THEN** the Server invokes the configured relation extractor with that Profile's exact relation policies

#### Scenario: General work has no relation policy
- **WHEN** an AfterRun turn uses `general-work`
- **THEN** the Server skips relation extraction without an extra model call or behavior change

#### Scenario: Agent is on another host
- **WHEN** Codex, Claude Code, or another command Hook Host sends the normal completed-turn payload using only its configured MCP URL and Token
- **THEN** all relation behavior occurs on the Server and the Agent Host needs no Server package or relation configuration

### Requirement: Relation model input is bounded and owner-scoped
The relation extractor SHALL receive the redacted source turn, Profile policy descriptions, and at most 40 trusted endpoint summaries. Every endpoint MUST be current, active, effective, owned by the authenticated principal, in the selected Profile, and of a type used by at least one relation policy; accepted same-capture memories SHALL be prioritized. The request MUST omit owner identity, bearer tokens, Evidence locations, and unrelated memories.

#### Scenario: Same-capture memories can be linked
- **WHEN** two candidate memories are both admitted as `AUTO_SAVE` and match a legal relation direction
- **THEN** both server-assigned stable memory IDs are eligible endpoint references before the atomic commit

#### Scenario: Pending or blocked candidate is proposed as an endpoint
- **WHEN** a candidate is pending, blocked, or discarded
- **THEN** it is absent from the endpoint catalog and cannot receive an automatic relation

#### Scenario: Another owner has a relevant-looking memory
- **WHEN** another owner's memory text appears relevant to the same turn
- **THEN** that memory is never included in the request and cannot be referenced by an accepted proposal

#### Scenario: Owner has more than the endpoint limit
- **WHEN** more than 40 effective policy-compatible memories exist
- **THEN** the Server deterministically prioritizes same-capture and turn-relevant records and sends no more than 40 endpoints

### Requirement: Relation model output is strict and untrusted
The relation extractor SHALL return at most 20 structured proposals containing only source memory ID, target memory ID, relation type, exact `source_expression`, `confidence`, and `expression_basis`. Extra fields, unknown endpoint IDs, self loops, unknown relation types, invalid directions, or source expressions absent from the redacted turn MUST fail with existing `invalid_candidate_output` semantics and MUST write no capture content.

#### Scenario: Model fabricates an owner field
- **WHEN** structured relation output includes owner, tenant, Token, Profile, or another undeclared identity field
- **THEN** strict schema validation rejects the output and no memory or relation from that capture is committed

#### Scenario: Model guesses a memory ID
- **WHEN** a proposal references an ID outside the trusted endpoint catalog
- **THEN** Core rejects the complete model output without revealing whether that ID exists for another owner

#### Scenario: Source expression is fabricated
- **WHEN** a proposal's `source_expression` is not an exact contiguous substring of the redacted turn
- **THEN** the capture fails safely and no relation is written

### Requirement: Automatic relation admission is conservative
The Server SHALL automatically persist only proposals whose `expression_basis` is `explicit`, confidence is at least `0.90`, source expression occurs in a redacted user message when structured messages are available, endpoints are distinct trusted records, and relation type/direction passes `ProfileRegistry.validate_relation`. Structurally valid proposals that originate only from Assistant/Tool text, are inferred, ambiguous, below threshold, or duplicated within the batch MUST be skipped without creating a pending relation or failing otherwise valid memories.

#### Scenario: Explicit evidence supports a thesis
- **WHEN** a proposal explicitly links an eligible `evidence_claim` to an eligible `thesis` with `supports` at confidence `0.90` or greater
- **THEN** one active relation is planned with trusted owner and Profile values

#### Scenario: Relation is only inferred
- **WHEN** the endpoints and relation type are legal but `expression_basis` is `inferred` or `ambiguous`
- **THEN** no automatic relation is written and admitted memories are still committed

#### Scenario: Assistant states a relationship on its own
- **WHEN** a structurally valid explicit relationship appears only in an Assistant or Tool message and not in any submitted user message
- **THEN** no automatic relation is written, preventing the Agent from reinforcing its own conclusion

#### Scenario: Duplicate proposals appear in one response
- **WHEN** the model emits the same source, target, and relation type more than once
- **THEN** at most one automatic relation is planned

### Requirement: Automatic relation writes share the Capture transaction
All accepted automatic relations SHALL be included in the same `CaptureWrite` transaction as new memories, replacements, duplicate Evidence, reviews, capture status, and outcomes. A transaction failure MUST expose neither partial memory nor partial relation data. Replaying a completed event MUST return the existing capture without invoking either extractor or creating another active relation.

#### Scenario: Two new memories are related
- **WHEN** both endpoints and their automatic relation are produced by one successful capture
- **THEN** PostgreSQL commits the endpoint rows before the relation inside one transaction and all become visible together

#### Scenario: Relation persistence fails
- **WHEN** Repository or database validation rejects an automatic relation during commit
- **THEN** the entire capture transaction rolls back with no new memory, review, outcome, or relation visible

#### Scenario: Completed AfterRun is replayed
- **WHEN** the same event ID and payload fingerprint are submitted again
- **THEN** the stored `CaptureResult` is returned with `replayed=true`, neither model is called, and the existing active relation count is unchanged

#### Scenario: An active relation already exists
- **WHEN** automatic extraction proposes an already-active source/target/type tuple
- **THEN** Repository idempotency preserves exactly one active relation and capture completion succeeds

### Requirement: Automatic relation failures and logs are safe
Invalid structured relation output SHALL use `FAILED/invalid_candidate_output`; unexpected Provider or processing interruption SHALL use `REPROCESS_REQUIRED/processing_interrupted`. Default operational logs SHALL contain only stable references, Profile/model/schema identifiers, counts, statuses, error codes, and duration, and MUST NOT contain relation source expressions, memory subjects/content, owner identities, credentials, or Evidence locations.

#### Scenario: Relation Provider call is interrupted
- **WHEN** relation extraction raises an unexpected runtime or network error
- **THEN** the capture is recorded as reprocess-required without committing candidate memories or relations

#### Scenario: Content logging is disabled
- **WHEN** automatic relation extraction and persistence complete under default logging
- **THEN** operational logs report endpoint/proposal/accepted counts without source turn, relation evidence text, memory content, owner, or Token values

### Requirement: Candidate and relation extraction share model configuration
The production composition root SHALL construct Candidate and Relation extractors from one configured ChatModel and the same Provider credentials, timeout, retry, and temperature settings, while assigning independent prompt and schema versions. No new required environment variable SHALL be introduced.

#### Scenario: Server starts with valid model settings
- **WHEN** the existing `MEMORY_MCP_MODEL_*` configuration is valid
- **THEN** one ChatModel is created and both strict extractors are available to Capture

#### Scenario: Tests inject only the existing candidate extractor
- **WHEN** a custom or test composition omits a `RelationExtractor`
- **THEN** legacy candidate capture remains compatible and safely skips automatic relations
