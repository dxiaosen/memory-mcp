## ADDED Requirements

### Requirement: Persist revision metadata without overstating certainty
Every newly saved MemoryRevision SHALL preserve extraction confidence, verification
status, sensitivity level, valid-from time, optional valid-until time, and optional
last-verified time. Extraction confidence MUST describe extraction quality rather than
factual truth. Existing revisions whose extraction confidence was never recorded MUST
remain explicitly unknown after migration.

#### Scenario: Explicit user statement is saved
- **WHEN** a high-confidence explicit user statement is automatically saved
- **THEN** its revision records the validated extraction confidence
- **AND** its verification status is `user_asserted`
- **AND** its sensitivity and validity defaults come from the active MemoryProfile

#### Scenario: Historical revision is migrated
- **WHEN** a revision created before metadata support is read after migration
- **THEN** its extraction confidence remains null
- **AND** it receives conservative verification, sensitivity, and validity defaults

### Requirement: Let profiles define metadata defaults without changing Core invariants
Every registered MemoryProfile MUST provide one metadata policy for every allowed
memory type. A policy SHALL declare a default sensitivity level and MAY declare a
positive default validity duration. Profile metadata MUST NOT redefine owner,
provenance, admission, idempotency, or lifecycle status semantics.

#### Scenario: Profile omits a memory type policy
- **WHEN** a Profile is registered without metadata policy for one of its memory types
- **THEN** registration fails before the Profile becomes available

#### Scenario: Profile defines a finite validity duration
- **WHEN** a candidate of that type is saved
- **THEN** Core derives its valid-until time from trusted event time and the Profile duration

### Requirement: Preserve citation-grade optional source metadata
Evidence SHALL retain the authenticated owner and completed-turn identifiers together
with source type, source expression, and optional URI, title, publisher, published time,
retrieved time, content hash, and citation locator. Source metadata MUST remain
attributable to the exact submitted message and MUST NOT establish owner identity or
factual verification by itself.

#### Scenario: Tool message includes a document citation
- **WHEN** a completed turn contains a tool message with valid document metadata
- **THEN** a resulting Evidence record preserves the citation fields and source expression
- **AND** later memory detail returns the same structured source metadata

#### Scenario: Source URI contains prohibited credential text
- **WHEN** source metadata contains content prohibited by the sensitive guard
- **THEN** the candidate is blocked before persistence
- **AND** the prohibited URI does not appear in storage, response, or logs

### Requirement: Keep prohibited content separate from sensitivity classification
The system MUST treat sensitivity level as metadata only for content allowed to be
stored. Content matching a prohibited persistence rule MUST remain blocked regardless
of the proposed or Profile-default sensitivity level.

#### Scenario: Restricted label is applied to a credential
- **WHEN** a candidate contains a credential and would otherwise receive `restricted`
- **THEN** the candidate is blocked instead of being saved as restricted memory

### Requirement: Enforce effective validity before ranking
Ordinary list and recall operations SHALL return only active current revisions whose
valid-from time is not in the future and whose valid-until time has not passed. This
filter MUST execute within the owner-scoped authoritative PostgreSQL candidate query
before application ranking.

#### Scenario: A future memory is relevant
- **WHEN** a relevant active revision has a valid-from time later than the current time
- **THEN** ordinary list and recall omit it

#### Scenario: A memory has passed its valid-until time
- **WHEN** a relevant active revision has reached or passed valid-until
- **THEN** it is excluded without requiring a background scheduler

### Requirement: Return explainable metadata to authenticated callers
Memory list, detail, history, and recall results SHALL expose extraction confidence,
verification status, sensitivity level, valid-from, valid-until, and last-verified time.
Recall source summaries SHALL expose permitted structured citation metadata while
preserving the existing token budget and safe historical-context header.

#### Scenario: Agent recalls a current research fact
- **WHEN** recall selects a metadata-bearing revision
- **THEN** the structured result contains its confidence, verification, sensitivity, and validity
- **AND** the rendered context identifies its verification and as-of boundary without presenting it as a new instruction

### Requirement: Allow an owner to revoke current memory
The service SHALL expose an idempotent `revoke_memory` MCP tool guarded by
`memory:review`. It MUST scope the target by authenticated owner, retain the current
revision and Evidence for explicit inspection, change its lifecycle status to
`revoked`, and immediately exclude it from ordinary list and recall.

#### Scenario: Owner revokes active memory
- **WHEN** an authenticated owner with review scope revokes an active memory identifier
- **THEN** the revision becomes revoked and remains traceable
- **AND** later ordinary recall does not return it

#### Scenario: Another owner guesses the identifier
- **WHEN** a different owner calls `revoke_memory` with that identifier
- **THEN** the result is indistinguishable from an unavailable memory
- **AND** no lifecycle state changes

#### Scenario: Owner retries a completed revoke
- **WHEN** the same owner repeats `revoke_memory` for an already revoked current revision
- **THEN** the tool returns the same revoked state without creating a revision or Evidence
