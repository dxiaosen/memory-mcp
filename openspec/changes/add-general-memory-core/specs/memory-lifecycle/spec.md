## ADDED Requirements

### Requirement: Maintain traceable memory records
Every active memory MUST retain at least one traceable source and enough context to identify its owning user, scenario, subject, memory type, content nature, formation time, current validity, and save rationale. Additional supporting expressions SHALL be attachable without losing earlier sources.

#### Scenario: A statement is repeated in a later conversation
- **WHEN** the same user repeats an equivalent durable statement in a later conversation
- **THEN** the system preserves one current memory
- **AND** the later expression is added as supporting source evidence

### Requirement: Separate admission, validity, and business progress
The system MUST represent candidate admission, memory validity, and scenario-specific business progress as separate concepts. Changing business progress MUST NOT implicitly change whether a memory is eligible for recall unless a scenario rule or explicit user action also changes validity.

#### Scenario: An active hypothesis is weakened
- **WHEN** evidence weakens a hypothesis that remains the user’s current subject of attention
- **THEN** the hypothesis may remain an active memory with weakened business progress
- **AND** it remains distinguishable from a superseded hypothesis

#### Scenario: An unresolved research question is saved
- **WHEN** the user clearly creates a research question that still lacks an answer
- **THEN** the memory is active
- **AND** its business progress is unresolved rather than pending user confirmation

### Requirement: Resolve duplicate and supplementary content
Within the same user scope, the system SHALL distinguish duplicate content from supplementary content. Duplicates MUST strengthen one logical memory; supplementary content MUST remain independently addressable and MAY be related to the original memory.

#### Scenario: Equivalent preference is repeated
- **WHEN** a candidate is semantically equivalent to an existing active preference for the same user and scope
- **THEN** the system does not create a second logical preference
- **AND** it records the new source as reinforcement

#### Scenario: A related concern is added
- **WHEN** a candidate adds a distinct concern without contradicting the existing memory
- **THEN** both memories remain available
- **AND** their supplementary relationship can be inspected

### Requirement: Handle conflict and replacement safely
The system SHALL distinguish explicit replacement from ambiguous conflict. An explicit user replacement MUST make the new memory current and preserve the old memory as history; an ambiguous conflict or model-inferred replacement MUST require confirmation before changing the current memory.

#### Scenario: User explicitly replaces a hypothesis
- **WHEN** a user explicitly says an old hypothesis is no longer primary and names a new hypothesis
- **THEN** the new hypothesis becomes active
- **AND** the old hypothesis becomes superseded history with a traceable replacement relationship

#### Scenario: New wording conflicts without clear intent
- **WHEN** a new statement conflicts with an active memory but does not clearly replace or correct it
- **THEN** the system creates a pending review item
- **AND** the active memory remains unchanged

### Requirement: Preserve historical validity
Superseded, expired, and revoked memories MUST remain distinguishable from active memories and MUST NOT be automatically used as current context. History SHALL retain the reason, time, source, and successor relationship when available.

#### Scenario: Time-limited matter expires
- **WHEN** a memory has an unambiguous validity deadline that has passed
- **THEN** it is excluded from current recall
- **AND** it remains available for historical review

#### Scenario: Event-based time remains ambiguous
- **WHEN** a memory is tied to an event whose actual date or completion is not known
- **THEN** the system does not expire it solely from an unsupported time inference
- **AND** it may request confirmation or retain the original time context

### Requirement: Apply lifecycle changes consistently
A lifecycle change affecting a memory and its relationships MUST be applied as one consistent operation. A failed update MUST NOT leave both an old and replacement memory simultaneously current or leave history without its required source.

#### Scenario: Replacement update fails partway
- **WHEN** an error occurs while applying an explicit replacement
- **THEN** the system preserves the last consistent state
- **AND** a later retry can safely complete the replacement

### Requirement: Support scenario-specific rules without changing the core lifecycle
The system SHALL allow a scenario to define its memory types, permitted relationships, business progress values, conflict rules, and recall priorities while preserving the common admission, provenance, validity, versioning, isolation, and audit behavior.

#### Scenario: A research-question scenario is added
- **WHEN** a scenario defines unresolved and resolved research-question progress
- **THEN** it can reuse the common capture, lifecycle, recall, and governance behavior
- **AND** adding the scenario does not redefine the core meaning of active, pending, superseded, expired, revoked, or deleted content
