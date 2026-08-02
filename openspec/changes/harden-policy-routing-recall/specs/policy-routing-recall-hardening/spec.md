## ADDED Requirements

### Requirement: Profile policy is versioned and auditable
The system MUST calculate a deterministic fingerprint for every effective memory Profile policy and MUST record `profile_version`, `profile_fingerprint`, and `prompt_version` on every new or reprocessed capture. Built-in Profiles MUST fail validation when their policy content differs from the fingerprint registered for the same Profile version.

#### Scenario: Built-in policy matches its registered version
- **WHEN** the server composes the built-in Profile registry
- **THEN** each Profile fingerprint MUST match the registered `(profile_id, profile_version)` fingerprint
- **AND** the Profile MUST become available for capture and recall

#### Scenario: Built-in policy changes without a version update
- **WHEN** a built-in Profile's effective policy content changes while its registered version and expected fingerprint remain unchanged
- **THEN** registry composition MUST fail before serving requests
- **AND** the failure MUST NOT log memory, prompt, token, or secret content

#### Scenario: Capture records policy provenance
- **WHEN** a completed turn is processed or reprocessed successfully
- **THEN** its capture audit record MUST contain the effective `profile_version`, `profile_fingerprint`, and `prompt_version`

### Requirement: Capture idempotency survives Profile upgrades
The system MUST treat an explicit capture event as unique by `(owner_id, event_id)` and a legacy event without `event_id` as unique by `(owner_id, profile_id, conversation_id, source_turn_id)`. `profile_version` MUST NOT be part of either logical identity.

#### Scenario: Explicit event retries after Profile upgrade
- **WHEN** the same owner retries the same `event_id` and unchanged payload after the Profile version has advanced
- **THEN** the system MUST return or resume the existing capture instead of creating another capture
- **AND** it MUST NOT create duplicate candidate or memory records

#### Scenario: Legacy event retries after Profile upgrade
- **WHEN** the same owner, Profile, conversation, source turn, and unchanged payload are retried without `event_id` after a Profile version upgrade
- **THEN** the system MUST return or resume the existing capture instead of creating another capture

#### Scenario: Idempotency key is reused for different content
- **WHEN** the same logical event identity is submitted with a different payload fingerprint
- **THEN** the request MUST fail with `idempotency_conflict`
- **AND** the original capture and memories MUST remain unchanged

#### Scenario: Different owners reuse an event ID
- **WHEN** two authenticated owners submit the same `event_id`
- **THEN** each owner MUST receive an independent capture scoped to that owner

### Requirement: Authenticated principal provides the default Profile
The server MUST associate every authenticated principal with a registered default Profile. Capture and recall requests that omit `profile_id` MUST use that trusted default; an explicit valid `profile_id` MUST remain available as an advanced override without changing owner scope.

#### Scenario: Lightweight Agent sends no Profile
- **WHEN** an Agent configured only with MCP URL and bearer Token invokes capture or recall without `profile_id`
- **THEN** the server MUST use the authenticated principal's `default_profile_id`
- **AND** the owner MUST still be derived exclusively from the authenticated principal

#### Scenario: Principal default is not registered
- **WHEN** production server configuration references a `default_profile_id` absent from the Profile registry
- **THEN** server composition MUST fail before accepting requests

#### Scenario: Advanced caller overrides the default
- **WHEN** an authorized caller explicitly supplies a registered `profile_id`
- **THEN** the server MUST execute the request with that Profile
- **AND** it MUST NOT alter the authenticated owner, tenant, or subject

#### Scenario: Agent configuration omits Profile
- **WHEN** the lightweight hook reads configuration without `MEMORY_HOOK_PROFILE_ID`
- **THEN** the Agent MUST omit `profile_id` from capture and recall tool arguments

### Requirement: Recall evaluation uses the production application path
The investment-memory benchmark MUST execute recall cases through the public production recall application service and MUST NOT import or invoke private scoring helpers.

#### Scenario: Recall case is evaluated
- **WHEN** the benchmark evaluates a recall case
- **THEN** it MUST create repository records through supported contracts and invoke the public recall service
- **AND** thresholding, lifecycle filtering, relation expansion, token budgeting, deduplication, and final ordering MUST be those of production code

#### Scenario: Query has no eligible memory
- **WHEN** all case memories are inactive, ineffective, expired, suppressed, or below the production threshold
- **THEN** the benchmark MUST observe an empty production recall result
- **AND** it MUST NOT synthesize a top-k result from raw relevance scores

### Requirement: Recall candidate reads are bounded
The application MUST pass a positive candidate limit through the memory repository port, and deployed repositories MUST apply that limit after owner, Profile, current revision, active lifecycle, effective time, and requested type or subject filters.

#### Scenario: PostgreSQL owner has more eligible memories than the limit
- **WHEN** recall is executed for an owner and Profile whose eligible rows exceed the configured candidate limit
- **THEN** PostgreSQL MUST return no more than that limit using deterministic ordering
- **AND** no row belonging to another owner or Profile may be included

#### Scenario: Candidate limit is invalid
- **WHEN** server configuration provides a zero or negative recall candidate limit
- **THEN** configuration validation MUST fail before serving requests

#### Scenario: Sensitive content remains out of operational logs
- **WHEN** default Profile routing, bounded recall, or policy fingerprint validation executes
- **THEN** operational logs MUST NOT contain conversation text, query text, candidate content, memory content, bearer Tokens, or secrets
