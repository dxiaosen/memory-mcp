## ADDED Requirements

### Requirement: Relationship origin and scope are explicit
The system SHALL classify every relationship as `legacy`, `manual`, or `automatic` and as `item` or `revision` scoped. New explicit `link_memories` relationships MUST be `manual/item`, new model-created relationships MUST be `automatic/revision`, and pre-migration records MUST remain `legacy/item` without fabricated provenance.

#### Scenario: Manual relationship survives a revision replacement
- **WHEN** an authenticated owner explicitly links two active memories and one endpoint later receives a replacement revision
- **THEN** the relationship remains active with `origin=manual` and `scope=item`

#### Scenario: Legacy relationship is migrated conservatively
- **WHEN** migration `0007` encounters a relationship created before provenance existed
- **THEN** it labels it `legacy/item` and does not invent revision or model evidence

### Requirement: Automatic relationship provenance is complete and trusted
Every `automatic/revision` relationship MUST identify its source and target revision, capture, conversation, source turn, exact redacted source expression, confidence, expression basis, model ID, prompt version, and schema version. The Repository MUST reject incomplete provenance, snapshots outside the trusted endpoints, cross-owner references, and snapshots that are not current at creation time.

#### Scenario: Accepted automatic relationship is auditable
- **WHEN** AfterRun admits a high-confidence explicit relationship from a user message
- **THEN** the committed relationship contains the exact trusted Capture identifiers, endpoint revision snapshots, source expression, confidence, expression basis, and extractor versions

#### Scenario: Model output cannot choose an owner or revision
- **WHEN** a relation proposal is admitted
- **THEN** owner, profile, capture and revision identities come only from trusted application records and not from model-provided identity fields

#### Scenario: Operational logs exclude provenance content
- **WHEN** an automatic relationship is planned, committed, made stale, read, or revoked
- **THEN** operational logs MUST NOT include owner text, source expression, conversation content, Token, or Secret values

### Requirement: Revision-scoped relationships become stale atomically
When a replacement revision is committed, every connected active revision-scoped relationship whose snapshot no longer matches MUST become `stale` with `stale_at` and reason `endpoint_revision_changed` in the same Repository transaction. Stale relationships MUST NOT participate in normal details, recall, relation boosting, or active uniqueness, but MUST remain available through explicit history.

#### Scenario: Automatic relationship stops following changed content
- **WHEN** an endpoint of an active automatic relationship receives a replacement revision
- **THEN** the old relationship becomes stale atomically and is absent from normal recall

#### Scenario: New relationship can replace a stale relationship
- **WHEN** a later capture establishes the same relation type between the current endpoint revisions
- **THEN** a new active relationship is created without deleting the stale history

#### Scenario: Replacement and stale transition roll back together
- **WHEN** persistence fails after a replacement or relationship transition begins
- **THEN** neither the new revision nor a partial stale/new relationship state becomes visible

### Requirement: Relationship governance remains owner scoped and backward compatible
Only the authenticated owner SHALL read or govern relationship provenance. `include_history=false` MUST return only effective active relationships, while `include_history=true` MAY return active, stale, and revoked relationships. Existing Agent URL/Token/Hook configuration and MCP tool names MUST remain unchanged.

#### Scenario: Cross-owner provenance is unavailable
- **WHEN** another owner guesses a relationship identifier or endpoint identifier
- **THEN** the service returns the same unavailable boundary as a missing relationship and reveals no provenance

#### Scenario: Stale relationship can be explicitly revoked
- **WHEN** the owner revokes a stale relationship
- **THEN** it becomes revoked idempotently while preserving its stale timestamp and reason for audit

