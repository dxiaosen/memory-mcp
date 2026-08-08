## ADDED Requirements

### Requirement: Capture completed records reconcilable candidate counts

The `memory.capture.completed` event SHALL include `extracted_candidate_count` (model's raw extracted proposals), `outcome_count` (total decisions produced), and `candidate_count` (proposals that passed validation and entered Candidate construction). The counts SHALL reconcile: `outcome_count == auto_saved_count + pending_count + discarded_count + blocked_count`, and `extracted_candidate_count >= outcome_count >= candidate_count`.

#### Scenario: One rejected among two extracted

- **WHEN** the model extracts 2 proposals, one fails source_expression validation (discard) and one is auto-saved
- **THEN** `extracted_candidate_count=2`, `outcome_count=2`, `candidate_count=1`, `auto_saved_count=1`, `discarded_count=1`, and `outcome_count == auto_saved + discarded`

### Requirement: Rejected candidates are visible in validation event

A `memory.capture.validation` content event SHALL be emitted after `memory.capture.candidates` and before `memory.capture.admission`, recording `extracted_candidate_count`, `validated_candidate_count`, and the full fields (`subject`, `content`, `source_expression`, `assertion_kind`, `expression_basis`, `reason_code`) of each proposal rejected at pre-validation (e.g. `invalid_source_expression`, `ambiguous_source_message`).

#### Scenario: Invalid source_expression is debuggable

- **WHEN** a proposal's source_expression is absent from the redacted source and is discarded
- **THEN** the `memory.capture.validation` event contains a `rejected` entry with that proposal's subject, content, source_expression, and reason_code `invalid_source_expression`

### Requirement: assertion_kind is normalized to agree with expression_basis

Candidate processing SHALL normalize `assertion_kind` against the trusted `source_type` and the model-reported `expression_basis`. For `tool`/`document`/`web` source types: `inferred` basis with a non-`system_inference` kind SHALL be corrected to `system_inference`; `explicit` basis with a `user_view`/`user_provided_fact`/`system_inference` kind SHALL be corrected to `external_fact`; `ambiguous` basis SHALL NOT be corrected. A DEBUG `memory.capture.candidate.assertion_normalized` event SHALL include `expression_basis`, `from_assertion_kind`, and `to_assertion_kind` when a correction is applied.

#### Scenario: Document fact with inferred basis becomes system_inference

- **WHEN** the model returns a candidate sourced from a document with `assertion_kind=external_fact` and `expression_basis=inferred`
- **THEN** the persisted candidate has `assertion_kind=system_inference`

### Requirement: source_uri is workspace-relative

The Agent Host Adapter SHALL convert file read paths to workspace-relative URIs relative to the hook-provided `cwd` before sending them as `source_uri` in document messages. When `cwd` is unavailable or the path cannot be relativized, the original path SHALL be retained.

#### Scenario: File under workspace becomes relative

- **WHEN** a Read tool_use has `file_path=/work/materials/04_纪要.md` and `cwd=/work`
- **THEN** the document message's `source_uri` is `materials/04_纪要.md`

### Requirement: Capture completed records per-stage durations

The `memory.capture.completed` event SHALL include `candidate_extraction_duration_ms`, `candidate_validation_duration_ms`, `admission_duration_ms`, `lifecycle_duration_ms`, `relation_duration_ms`, and `persistence_duration_ms`. Stages not executed on a given path SHALL be recorded as 0.

#### Scenario: All six stages recorded

- **WHEN** a capture completes with candidates and a relation planner configured
- **THEN** `memory.capture.completed` records all six stage durations as non-negative values

### Requirement: Capture log events follow business execution order

Capture content and info events SHALL be emitted in business execution order: `started` -> `input` -> `candidates` -> `validation` -> `admission` -> `relations_planned` -> `relation_candidates` -> `persisted` -> `completed`. `relations_planned` SHALL NOT be emitted before `admission`.

#### Scenario: relations_planned follows admission

- **WHEN** a capture with candidates and relations completes
- **THEN** in the log stream, `memory.capture.admission` appears before `memory.capture.relations_planned`
