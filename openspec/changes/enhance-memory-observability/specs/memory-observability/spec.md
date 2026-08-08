## ADDED Requirements

### Requirement: Recall candidate stage is visible at INFO level

The `memory.recall.candidates` event SHALL be emitted at INFO level (not DEBUG) so that the recall pipeline (started → candidates → ranked → output → completed) is fully visible at the default log level. The event SHALL include `recall_ref`, `candidate_count`, `candidate_limit`, `lexical_count`, `vector_count`, `recent_count`, `profile_id`, and `embedding_degraded`.

#### Scenario: Zero-recall candidates visible at INFO

- **WHEN** a recall query matches no memories and the default log level is INFO
- **THEN** the logs contain a `memory.recall.candidates` event at INFO with `candidate_count=0`

### Requirement: Recall completed records per-stage durations

The `memory.recall.completed` event SHALL include `query_embedding_duration_ms`, `repository_candidate_duration_ms`, `ranking_duration_ms`, `evidence_loading_duration_ms`, and `render_duration_ms`. Stages not reached on a given code path (e.g. evidence loading and rendering on a zero-result path) SHALL be recorded as 0.

#### Scenario: Zero-result path omits evidence and render stages

- **WHEN** recall yields zero candidates and completes via the zero-result early-return
- **THEN** `memory.recall.completed` records `query_embedding_duration_ms >= 0`, `repository_candidate_duration_ms >= 0`, `ranking_duration_ms >= 0`, `evidence_loading_duration_ms == 0`, and `render_duration_ms == 0`

#### Scenario: Full path records all stages

- **WHEN** recall yields candidates that pass the threshold and are rendered into context
- **THEN** `memory.recall.completed` records all five stage durations as non-negative values
