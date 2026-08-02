## ADDED Requirements

### Requirement: Bounded automatic maintenance
The Server MUST run owner-agnostic maintenance internally at a bounded configurable interval without exposing a public maintenance MCP tool, and each repository transaction MUST process no more than the fixed batch size.

#### Scenario: Server performs maintenance without Agent configuration
- **WHEN** the Server lifespan is running with a positive maintenance interval
- **THEN** maintenance runs without any Agent owner selector, Hook change, queue, or additional Agent credential

#### Scenario: Maintenance is explicitly disabled
- **WHEN** `MEMORY_MCP_MAINTENANCE_INTERVAL_SECONDS` is `0`
- **THEN** the Server does not start the periodic maintenance task and normal MCP behavior remains available

### Requirement: Expired memory state is materialized safely
Maintenance MUST change only current `active` revisions whose `valid_until` is at or before the trusted effective time to `expired`, MUST preserve the MemoryItem, revision and Evidence history, and MUST be idempotent under replay.

#### Scenario: Active revision reaches its validity boundary
- **WHEN** maintenance observes a current active revision with `valid_until <= effective_at`
- **THEN** it marks that revision `expired` and ordinary list/recall continue to exclude it

#### Scenario: Maintenance is replayed
- **WHEN** maintenance processes an already expired, superseded or revoked revision
- **THEN** it does not change that revision or count it as a new transition

#### Scenario: Concurrent lifecycle update wins the row lock
- **WHEN** replacement, revoke or another maintenance worker changes a target before the conditional update
- **THEN** maintenance does not overwrite the newer legal state

### Requirement: Pending review lifecycle terminates
Maintenance MUST move a `pending` review to `expired` when its candidate validity has ended or it exceeds the fixed pending-review retention window, MUST record the trusted decision time, and MUST NOT create or alter a memory from that review.

#### Scenario: Pending review expires
- **WHEN** a pending review reaches either expiration condition
- **THEN** it becomes `expired`, has `decided_at` set, has no `resolved_memory_id`, and is absent from pending review lists

#### Scenario: Client addresses an expired review
- **WHEN** a client tries to confirm or reject an expired review
- **THEN** the service returns the existing unavailable-review behavior and creates no memory

### Requirement: Relations follow expired endpoints
The maintenance transaction MUST mark active relations attached to a newly expired endpoint as `stale` with reason `endpoint_expired`, while preserving relation provenance and history.

#### Scenario: Revision endpoint expires
- **WHEN** maintenance expires a revision that participates in an active item- or revision-scoped relation
- **THEN** that relation becomes stale in the same transaction and no longer contributes to recall

### Requirement: Maintenance is observable without content disclosure
Each maintenance run MUST emit structured operational data for duration, transition counts, continuation state and failure class, and MUST NOT emit owner identifiers, memory content, query text, Evidence, bearer tokens or secrets.

#### Scenario: Successful batch is logged
- **WHEN** a maintenance batch completes
- **THEN** operators can observe its counts and whether more work remains without seeing application content

#### Scenario: Batch fails
- **WHEN** a maintenance batch raises a database or runtime error
- **THEN** the Server logs the failure class, remains available, and retries only on a later runner iteration
