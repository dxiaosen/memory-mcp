## ADDED Requirements

### Requirement: Agent uses separate recall and capture HTTP timeouts

The Agent client SHALL use distinct HTTP timeouts for recall (`recall_timeout_seconds`, default 15s) and capture (`capture_timeout_seconds`, default 70s). The capture timeout SHALL exceed the Server's normal capture processing P95 so that a normally-processing request never triggers a retry. Both timeouts SHALL be configurable via `MEMORY_HOOK_RECALL_TIMEOUT_SECONDS` and `MEMORY_HOOK_CAPTURE_TIMEOUT_SECONDS`.

#### Scenario: Normal capture does not retry

- **WHEN** a `capture_completed_turn` request completes in 30–40 seconds
- **THEN** the Agent issues exactly one `CallToolRequest`, the Server emits exactly one `memory.capture.completed`, and `memory.capture.replay` is not emitted

#### Scenario: Capture timeout exceeds recall timeout

- **THEN** `capture_timeout_seconds` default (70) is greater than `recall_timeout_seconds` default (15)

### Requirement: Retries only after a completed attempt, never concurrent

The Agent SHALL retry a capture only after the previous attempt has completed (success or explicit failure). It MUST NOT start a new attempt while a previous attempt for the same `event_id` is still in-flight. Retries are bounded by `capture_max_attempts`.

#### Scenario: First attempt still in-flight

- **WHEN** the first capture attempt is still awaiting a response
- **THEN** the Agent does not send a second request for the same `event_id`

### Requirement: Agent records per-attempt debugging events

The Agent SHALL emit `agent_hook.capture.attempt.started` (with `event_ref`, `attempt`, `timeout_seconds`), `agent_hook.capture.attempt.completed` (with `attempt`, `duration_ms`, `replayed`, `status`), and `agent_hook.capture.attempt.failed` (with `attempt`, `duration_ms`, `error_type`, `retryable`) for each capture attempt, so that repeated requests can be attributed to a specific attempt from the logs without inferring from timestamps.

#### Scenario: Three attempts visible from logs

- **WHEN** a capture is attempted three times
- **THEN** the logs contain three `attempt.started` / `attempt.completed`-or-`attempt.failed` records with `attempt=1,2,3`

### Requirement: Single invalid source_expression discards only that candidate

Candidate processing SHALL NOT fail an entire capture when a single proposal's `source_expression` does not occur in the redacted source turn. It SHALL discard only that candidate with `decision=discard` and `reason_code=invalid_source_expression`, and continue processing the remaining proposals. The discarded count SHALL be reflected in `memory.capture.completed.discarded_count` and `reason_counts`.

#### Scenario: One bad candidate among several

- **WHEN** a capture batch contains one proposal whose `source_expression` is absent from the redacted source and two valid proposals
- **THEN** the capture completes with `candidate_count=2`, `discarded_count=1`, and `reason_counts` containing `invalid_source_expression: 1`

#### Scenario: User research baseline is not lost

- **WHEN** a user explicitly states a long-term research baseline and the model produces one proposal with an unmatched `source_expression` alongside valid proposals
- **THEN** the valid proposals are still admitted (pending or auto-saved) and the capture does not return zero candidates solely due to the one bad proposal

### Requirement: invalid_output records offending field context in dev

When `memory.capture.invalid_output` is emitted for a structured-output violation (confidence out of range, non-object candidate, invalid UUID, etc.), the event SHALL include the offending field context (e.g. `source_expression`, `candidate_index`, `candidate_subject`, or the specific field and value) so that `error_detail` is not null during the development phase.

#### Scenario: Confidence out of range

- **WHEN** the model returns a candidate with `confidence=1.5`
- **THEN** `memory.capture.invalid_output.error_detail` is non-null and identifies the `confidence` field and value
