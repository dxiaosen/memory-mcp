## ADDED Requirements

### Requirement: Relation validation failures are graded fatal vs non-fatal

The relation planner SHALL classify admission rejections into fatal and non-fatal. Fatal rejections (`invalid_source_expression`, `relation_endpoint_outside_catalog`, and extractor `InvalidModelOutputError`) SHALL trigger a bounded retry and, when all attempts fail, SHALL raise `InvalidModelOutputError` causing the capture to fail atomically. Non-fatal rejections (`relation_policy_mismatch`, `relation_low_confidence`, `relation_not_explicit`, `relation_insufficient_evidence`, `relation_negated`, `relation_reversed_direction`, `relation_duplicate`, `relation_non_user_source`) SHALL be skipped (counted in `skipped_count`) WITHOUT retry and WITHOUT failing the capture; the capture SHALL continue and persist candidates/memories normally. An attempt with no fatal rejections SHALL complete successfully even when non-fatal skips exist.

#### Scenario: Policy mismatch does not fail capture

- **WHEN** a relation proposal has a relation_type / endpoint-type combination not allowed by the profile policy
- **THEN** the proposal is skipped with reason_code `relation_policy_mismatch`, the capture completes, candidates are persisted, the extractor is invoked exactly once (no retry), and no `memory.capture.incomplete` is written

#### Scenario: Low confidence does not fail capture

- **WHEN** a relation proposal has confidence 0.7 (< 0.90)
- **THEN** the proposal is skipped with `relation_low_confidence`, the capture completes, the extractor is invoked once, and no relation is written

#### Scenario: Invalid source_expression still fails closed

- **WHEN** every attempt produces a proposal whose source_expression is absent from the source
- **THEN** the capture is `FAILED` with `failure_code=invalid_candidate_output`, the extractor is invoked `max_attempts` times, and no memory or relation is persisted

### Requirement: Relation exception message and reason code are consistent

When the relation planner raises `InvalidModelOutputError` after all fatal attempts fail, the exception message SHALL reflect the actual fatal reason code(s) (e.g. `relation validation failed: invalid_source_expression`), not a hardcoded or stale message. The `memory.capture.relation_validation_rejected` content event SHALL record each rejected/skipped proposal's true `reason_code`, and the `relation_extraction_attempt.failed` event's `error_message` SHALL match. The `retryable` flag SHALL be `true` for fatal rejections (until the last attempt) and non-fatal skips SHALL NOT emit a `failed` event.

#### Scenario: Fatal failure message matches reason

- **WHEN** all attempts fail with `invalid_source_expression`
- **THEN** the raised `InvalidModelOutputError` message contains `invalid_source_expression`, and the final `relation_extraction_attempt.failed` has `retryable=false`

### Requirement: Relation auto-save threshold is unchanged

The relation auto-save confidence threshold SHALL remain `>= 0.90`. A relation below threshold SHALL be skipped (non-fatal), never accepted to raise success nor cause capture failure.

#### Scenario: Below-threshold relation skipped not accepted

- **WHEN** a relation proposal has confidence 0.7
- **THEN** it is skipped (`relation_low_confidence`), `relation_accepted_count` is 0, and the capture is not failed
