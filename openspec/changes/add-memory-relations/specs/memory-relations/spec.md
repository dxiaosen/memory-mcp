## ADDED Requirements

### Requirement: Profiles declare exact relation policies
Every `MemoryProfile` SHALL expose one `relation_policies` mapping. Each relation type MUST be normalized and map to a `MemoryRelationPolicy` whose source and target sets are non-empty subsets of that Profile's memory types. The registry MUST reject malformed relation policies and recall priority mappings before persistence registration.

#### Scenario: A valid Profile has no relations
- **WHEN** `general-work` registers with an empty `relation_policies` mapping
- **THEN** registration succeeds and no relation type is available for that Profile

#### Scenario: A relation endpoint type is invalid
- **WHEN** a Profile policy references a source or target memory type outside its own vocabulary
- **THEN** registration fails without mutating the runtime registry or persistent Profile catalog

#### Scenario: Recall priorities drift from memory types
- **WHEN** a Profile omits a memory type from `recall_priorities`, adds an unknown key, or supplies a negative or non-integer priority
- **THEN** registration fails before the Profile can serve capture or recall

### Requirement: Investment research defines bounded relation semantics
The `investment-research` Profile SHALL define only `supports`, `challenges`, `threatens`, `could_catalyze`, `addresses`, and `resolves` with the endpoint directions specified by its relation policies. Generic Core code MUST NOT branch on those business names.

#### Scenario: Evidence supports a thesis
- **WHEN** an owned `evidence_claim` is linked to an owned `thesis` with `supports`
- **THEN** the relation passes Profile validation and is persisted

#### Scenario: A relation direction is invalid
- **WHEN** the same two memories are submitted as `thesis supports evidence_claim`
- **THEN** the system rejects the request with stable `INVALID_RELATION` semantics and writes no relation

#### Scenario: General work does not inherit research relations
- **WHEN** a client tries to create `supports` between two `general-work` memories
- **THEN** the system rejects the relation without adding investment-specific logic to Core

### Requirement: Relations are owner-scoped stable item links
A `MemoryRelation` SHALL link two distinct stable `MemoryItem` identifiers owned by the authenticated principal and belonging to the same Profile. It SHALL record a stable relation ID, relation type, `active` or `revoked` status, creation time, and optional revocation time. Creating a relation MUST require both endpoints to have current, active, effective revisions.

#### Scenario: A valid owned relation is created
- **WHEN** an authenticated owner links two effective memories under a legal Profile policy
- **THEN** one active relation is committed and points to the MemoryItem IDs rather than revision IDs

#### Scenario: Replacement preserves a relation
- **WHEN** either endpoint receives a replacement revision
- **THEN** the active relation remains attached to the stable MemoryItem and detail returns the new current revision with that relation

#### Scenario: A relation crosses owners or Profiles
- **WHEN** either endpoint belongs to another owner or a different Profile
- **THEN** no relation is created and guessed identifiers reveal no cross-owner memory data

#### Scenario: A relation is a self loop
- **WHEN** source and target memory IDs are equal
- **THEN** the request is rejected and no relation is written

#### Scenario: An endpoint is inactive or ineffective
- **WHEN** either endpoint is revoked, expired, not yet valid, or lacks an active current revision
- **THEN** relation creation fails without reactivating or modifying the endpoint

### Requirement: Relation writes are idempotent and auditable
The system SHALL expose owner-scoped idempotent create and revoke operations. Replaying creation for the same owner, source, target, and relation type MUST return the existing active relation. Revocation MUST preserve the row and set a trusted revocation time; replaying revocation MUST return the same revoked relation.

#### Scenario: Link creation is replayed
- **WHEN** the same legal relation is submitted repeatedly or concurrently
- **THEN** exactly one active relation exists and every successful caller receives that logical relation

#### Scenario: Revocation is replayed
- **WHEN** an owner revokes an active relation and repeats the operation
- **THEN** the original relation remains `revoked` with its original revocation time

#### Scenario: Another owner guesses a relation ID
- **WHEN** a principal tries to revoke a relation owned by someone else
- **THEN** the result is indistinguishable from a missing relation and the owned row is unchanged

### Requirement: PostgreSQL enforces relation ownership and vocabulary
The deployed PostgreSQL schema SHALL enforce registered relation types, same-owner and same-Profile endpoints, non-empty relation types, no self loops, valid state/time combinations, and at most one active duplicate relation. Repository queries MUST begin with trusted owner scope and use indexes for owner/Profile and endpoint access.

#### Scenario: An adapter bypass attempts a cross-owner insert
- **WHEN** a direct Repository write pairs endpoint identities from different owners
- **THEN** transaction validation or a database constraint rejects the write atomically

#### Scenario: An unregistered relation type is inserted
- **WHEN** a relation type is absent from the persistent Profile relation catalog
- **THEN** the database rejects the row and no partial relation is visible

#### Scenario: Old Server runs after forward migration
- **WHEN** code that predates relation support runs against a schema containing `0006_memory_relations.sql`
- **THEN** existing capture, review, list, detail, revoke, and recall operations continue without requiring relation data

### Requirement: MCP exposes relation operations without owner selectors
The MCP Server SHALL expose `link_memories` under `memory:write` and `revoke_memory_relation` under `memory:review`. Neither tool SHALL accept owner, tenant, subject identity, client identity, or bearer token arguments. Invalid policy direction SHALL return `INVALID_RELATION`; missing or cross-owner relation IDs SHALL return `RELATION_UNAVAILABLE`.

#### Scenario: A writer links memories
- **WHEN** an authenticated principal with `memory:write` calls `link_memories` with two owned IDs and a legal type
- **THEN** the Server derives owner and Profile from trusted context/endpoints and returns a structured relation receipt

#### Scenario: A reader lacks write scope
- **WHEN** a principal with only `memory:read` calls `link_memories`
- **THEN** the tool rejects the call before any Core mutation

#### Scenario: A writer lacks review scope
- **WHEN** a principal with write but no `memory:review` calls `revoke_memory_relation`
- **THEN** the tool rejects the call and the relation remains active

#### Scenario: Extra identity fields are submitted
- **WHEN** a client adds an undeclared owner or tenant field to either relation tool
- **THEN** strict MCP argument validation rejects the payload

### Requirement: Detail and recall return safe one-hop relations
`get_memory` SHALL return active one-hop relation summaries and SHALL include revoked relation history only when `include_history=true`. `recall_memory` SHALL load relations only after owner/Profile/current/active/effective candidate filtering, MAY apply a bounded one-hop relevance boost only when the other endpoint is independently relevant, and MUST keep all rendered content within `max_items` and `token_budget`.

#### Scenario: Detail returns relation direction
- **WHEN** an owner reads either endpoint of an active relation
- **THEN** the relation summary identifies `outgoing` for the source, `incoming` for the target, and includes no other endpoint content beyond stable ID, subject, and memory type

#### Scenario: Revoked relation history is requested
- **WHEN** an owner calls `get_memory` with `include_history=true` after revoking a relation
- **THEN** the response includes the revoked relation and its revocation time while the default detail response excludes it

#### Scenario: A related candidate is independently relevant
- **WHEN** two effective candidates are related and both meet the base relevance threshold
- **THEN** relation weighting deterministically improves their score by no more than the configured one-hop cap

#### Scenario: A relation points to an irrelevant candidate
- **WHEN** only one endpoint meets the base relevance threshold
- **THEN** the relation alone does not pull the other endpoint into recall

#### Scenario: Relation rendering exceeds the budget
- **WHEN** relation metadata and memory text would exceed `token_budget`
- **THEN** the Server truncates whole result items/relations safely and reports `truncated` without exceeding the budget

### Requirement: Relation handling preserves logging and dependency boundaries
Operational relation logs SHALL contain only stable relation/memory references, status, counts, error codes, and duration. Domain/application/ports MUST NOT import MCP, HTTP, PostgreSQL drivers, runtime settings, Agent SDKs, or investment Profile implementations.

#### Scenario: Content logging is disabled
- **WHEN** link, revoke, detail, or recall relation operations run under default logging
- **THEN** relation descriptions, memory content, subjects, owner identities, bearer tokens, and source expressions do not appear in operational logs

#### Scenario: Core is imported independently
- **WHEN** dependency-boundary tests import Core relation models and services
- **THEN** no transport, database-driver, Agent, or investment-specific dependency is loaded through those modules
