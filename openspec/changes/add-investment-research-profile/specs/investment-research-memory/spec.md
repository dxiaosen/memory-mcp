## ADDED Requirements

### Requirement: Provide a formal investment research memory profile
The service SHALL provide and register an `investment-research` MemoryProfile whose
allowed atomic memory types are `research_preference`, `research_question`, `thesis`,
`evidence_claim`, `risk`, `catalyst`, `ongoing_research`, and `research_decision`.
The Profile MUST use the generic Memory Core contracts and MUST NOT add investment
branches to Core domain, application, repository, or Agent Host code.

#### Scenario: Research integration selects the profile
- **WHEN** an authenticated integration captures a completed turn with `profile_id` set to `investment-research`
- **THEN** the extractor receives exactly the investment research memory types and Profile guidance
- **AND** resulting memories remain scoped to the authenticated owner

#### Scenario: General work remains the default
- **WHEN** a caller omits `profile_id`
- **THEN** the existing `general-work` default remains in effect
- **AND** the service does not infer or switch Profile from conversation content

### Requirement: Keep research candidates atomic and semantically distinct
The Profile guidance MUST distinguish durable research preferences, unresolved
questions, falsifiable user theses, externally sourced evidence claims, risks,
catalysts, ongoing research work, and research decisions. Each candidate SHALL express
one independently replaceable proposition and use a subject precise enough that
unrelated metrics, periods, risks, or questions can coexist.

#### Scenario: Thesis and evidence coexist for one entity
- **WHEN** a turn contains an explicit user thesis and a separately sourced supporting metric for the same entity
- **THEN** they can be captured as `thesis` and `evidence_claim` without lifecycle conflict
- **AND** the thesis remains a user view while the metric remains an external fact with its own Evidence

#### Scenario: Same atomic thesis conflicts
- **WHEN** a later candidate has the same owner, Profile, subject, and `thesis` type but incompatible content without an explicit replacement
- **THEN** the generic lifecycle policy keeps the conflict pending
- **AND** no Profile-specific overwrite path bypasses confirmation

### Requirement: Apply conservative research metadata policies
The Profile MUST define a sensitivity and validity policy for every allowed type.
`research_preference` and `research_decision` SHALL default to confidential with no
automatic expiry. `research_question` and `ongoing_research` SHALL default to 365 days;
`thesis` and `risk` to 180 days; and `evidence_claim` and `catalyst` to 90 days.
`evidence_claim` and `catalyst` SHALL default to internal sensitivity and the other
finite types to confidential sensitivity.

#### Scenario: External evidence ages out of ordinary recall
- **WHEN** an `evidence_claim` reaches 90 days after its trusted observed time
- **THEN** ordinary list and recall omit it through the generic effective-time filter
- **AND** its revision and citation Evidence remain available for explicit inspection

#### Scenario: Research preference remains durable
- **WHEN** a `research_preference` is stored without a later revoke or replacement
- **THEN** it has no Profile-derived valid-until time
- **AND** it remains eligible for future recall

### Requirement: Preserve evidence and verification boundaries
An `evidence_claim` SHALL use `external_fact` and SHALL preserve every available
document, web, or tool Evidence field. A citation or high extraction confidence MUST NOT by
itself assign `source_verified`; non-user candidates MUST continue through the generic
pending path, and recall MUST retain verification and validity labels.

#### Scenario: Cited tool result proposes a research fact
- **WHEN** a tool message proposes an `evidence_claim` with source URI, title, publisher, time, hash, or locator
- **THEN** the candidate remains unverified until an explicit confirmation path changes its status
- **AND** persisted Evidence preserves the submitted citation metadata

#### Scenario: Extraction confidence is high
- **WHEN** a research candidate has high model extraction confidence but no factual verification
- **THEN** confidence records extraction quality only
- **AND** recall does not present the candidate as a verified external fact

### Requirement: Keep prohibited financial content outside memory
The Profile MUST NOT weaken the common sensitive persistence guard. Real holdings,
credentials, and transaction instructions MUST be blocked even when they otherwise
match a research type or a restrictive sensitivity label.

#### Scenario: Transaction instruction resembles a research decision
- **WHEN** a candidate classified as `research_decision` contains a buy, sell, or order instruction prohibited by the common guard
- **THEN** capture returns a blocked outcome
- **AND** no candidate content, revision, or Evidence is persisted

### Requirement: Support research progress without pretending relations exist
The Profile SHALL allow only `open`, `monitoring`, `resolved`, `invalidated`, and
`archived` as non-null business progress values. It MUST NOT declare executable
relations until the Core provides a persisted and enforced relation contract.

#### Scenario: Unsupported research progress is proposed
- **WHEN** a research candidate contains a non-null progress value outside the Profile vocabulary
- **THEN** capture fails safely as invalid candidate output
- **AND** no partial research memory is persisted
