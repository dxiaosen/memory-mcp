## ADDED Requirements

### Requirement: Recall candidate retrieval is bounded and source-lazy
The Repository SHALL filter by trusted owner, Profile, current active effectiveness and optional subject before applying a strict candidate limit. Candidate retrieval SHALL NOT load Evidence. After deterministic ranking and token/item selection, the application SHALL load at most the latest three Evidence rows for each selected revision in one Repository operation.

#### Scenario: Large candidate window
- **WHEN** a recall query produces 500 eligible candidates but selects five items
- **THEN** candidate retrieval performs no per-candidate Evidence query and the final hydration operation loads Evidence only for the selected revisions

#### Scenario: Owner isolation during hydration
- **WHEN** selected revision identifiers include a value owned by another principal
- **THEN** the Evidence hydration operation returns no source owned by the other principal

### Requirement: Recall behavior is verified against realistic corpora
The evaluation suite MUST include empty recall, semantic paraphrase, entity and reporting-period hard negatives, an older durable target and a corpus large enough to exercise the candidate bound. A dedicated PostgreSQL contract SHALL execute the actual lexical/recent query and Evidence hydration rather than only inspecting SQL text.

#### Scenario: No relevant memory
- **WHEN** a query has only unrelated eligible memories
- **THEN** recall returns no items and does not inject unrelated context

#### Scenario: PostgreSQL implementation verification
- **WHEN** the explicit disposable PostgreSQL test database is configured
- **THEN** the suite executes hybrid retrieval, per-revision Evidence limits, owner isolation and maintenance idempotency against PostgreSQL

### Requirement: Model-assisted retrieval is evidence-gated
The Server MUST NOT add a mandatory model call to BeforeRun recall solely to make fixed tests pass. Model query expansion, Embedding or reranking SHALL require a recorded benchmark gap and MUST remain fail-safe when its provider is unavailable.

#### Scenario: Current deterministic benchmark passes
- **WHEN** the expanded deterministic benchmark meets its recall and safety thresholds
- **THEN** the release keeps Recall independent of the model provider

