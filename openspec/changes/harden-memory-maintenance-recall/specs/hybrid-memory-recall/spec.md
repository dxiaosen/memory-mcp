## ADDED Requirements

### Requirement: Owner-first hybrid candidate retrieval
Recall MUST form candidates only after enforcing trusted owner, Profile, current revision, `active` lifecycle and effective validity predicates, and MUST combine indexed lexical candidates with bounded recent candidates without reading another owner or Profile into Application memory.

#### Scenario: Older lexical match competes with recent memories
- **WHEN** a relevant older memory is outside the recent quota but matches the recall query lexically
- **THEN** it enters the bounded candidate set and competes in the existing Application ranking

#### Scenario: Another owner has a stronger match
- **WHEN** another owner has content with a higher lexical similarity
- **THEN** that record is never loaded, scored, logged or returned for the authenticated owner

#### Scenario: Another Profile has a stronger match
- **WHEN** the current owner has a higher-similarity record under a different Profile
- **THEN** that record is excluded before lexical and recent candidate selection

### Requirement: Hybrid retrieval remains bounded and deterministic
The Repository MUST return no more than `MEMORY_MCP_RECALL_CANDIDATE_LIMIT`, MUST reserve bounded capacity for lexical and recent retrieval, and MUST use stable tie-breakers. Application ranking MUST retain its relevance threshold, Profile signals, relation safeguards, `max_items` and token budget.

#### Scenario: Candidate population exceeds the limit
- **WHEN** eligible lexical and recent records together exceed the configured candidate limit
- **THEN** the Repository returns at most that limit in stable order and Application result limits remain unchanged

#### Scenario: Lexical and recent groups overlap
- **WHEN** the same memory qualifies for both groups
- **THEN** it appears once and unused capacity is filled from the recent group when available

### Requirement: PostgreSQL lexical retrieval is index-backed
The PostgreSQL migration MUST provision `pg_trgm` and partial trigram indexes for current active subject/content lookup, and the deployed Repository MUST use trigram predicates within the eligible owner/Profile scope.

#### Scenario: New migration is applied
- **WHEN** the database migration completes
- **THEN** required extension and indexes exist before the new Server performs hybrid recall

### Requirement: Recall correctness does not depend on a model
Maintenance and recall MUST remain functional when the configured extraction chat model is unavailable, and this change MUST NOT add a mandatory LLM or embedding call to the BeforeRun recall path.

#### Scenario: Model provider is unavailable during recall
- **WHEN** the model endpoint is unavailable but PostgreSQL is healthy
- **THEN** hybrid recall still returns deterministic results or an empty result according to the query and stored memories

### Requirement: Retrieval telemetry protects sensitive data
Operational recall telemetry MUST expose candidate counts and retrieval-path counts but MUST NOT expose owner identifiers, query text, memory content, Evidence, bearer tokens or secrets unless the existing explicit content-logging mode is enabled.

#### Scenario: Production content logging is disabled
- **WHEN** a hybrid recall completes under normal production logging
- **THEN** logs contain only bounded counts, timing, Profile identifier and safe technical metadata
