## MODIFIED Requirements

### Requirement: Capture the completed top-level turn

The `capture_completed_turn` MCP tool MAY run in either synchronous or asynchronous (queue)
mode, controlled by the `MEMORY_MCP_CAPTURE_ENQUEUE_ENABLED` setting (default `true`).

In **asynchronous mode** (default), the tool SHALL call `enqueue_capture` which performs only
identity/idempotency verification, sensitive-content redaction, and a single `PENDING` row
write containing the redacted `content` and `subject_hint`. The tool SHALL return immediately
with `status="pending"` and empty outcome counts—the model extraction has not yet run. A
same-process background worker (`_run_capture_reprocess_loop`) SHALL periodically pick up
`PENDING` rows via `list_pending_captures` (using `FOR UPDATE SKIP LOCKED` for concurrent
safety), run the structured extraction, and overwrite the row to a terminal status
(`completed` / `reprocess_required` / `failed`) via `commit_capture`.

In **synchronous mode** (`capture_enqueue_enabled=false`), the tool SHALL call `capture_turn`
which performs the full extraction inline before returning, preserving the pre-queue contract.

#### Scenario: Asynchronous capture returns pending immediately

- WHEN the client calls `capture_completed_turn` with a valid turn and
  `capture_enqueue_enabled` is `true`
- THEN the tool SHALL return a `CaptureReceipt` with `status` equal to `"pending"`,
  `replayed` equal to `false`, and all `summary` counts equal to `0`
- AND the server SHALL NOT have invoked the candidate extractor for this turn

#### Scenario: Worker completes pending capture

- GIVEN a `PENDING` capture row exists
- WHEN the capture-reprocess worker runs one batch
- THEN the row SHALL transition to `completed` (or `reprocess_required`/`failed` on error)
- AND a subsequent `recall_memory` for the turn's subject SHALL return the extracted memory

#### Scenario: Idempotent replay of pending row

- WHEN `capture_completed_turn` is called twice with the same `event_id` and
  `payload_fingerprint` while the row is still `PENDING`
- THEN the second call SHALL return `replayed=true` with the same `capture_id`
- AND no new row SHALL be created

#### Scenario: Concurrent worker pickup

- GIVEN multiple worker instances and multiple `PENDING` rows
- WHEN each worker calls `list_pending_captures`
- THEN no two workers SHALL receive the same row (`FOR UPDATE SKIP LOCKED`)

## ADDED Requirements

### Requirement: Capture status PENDING

The `CaptureStatus` enumeration SHALL include `pending` representing a capture that has been
enqueued for asynchronous extraction but not yet processed. A `pending` capture SHALL have
`failure_code` equal to `NULL` and an empty `outcomes` tuple. The `reprocess_required`
status remains reserved for captures that failed extraction and are eligible for retry.

#### Scenario: Pending capture validates no failure code or outcomes

- WHEN a `CaptureResult` is constructed with `status=pending`
- THEN it SHALL be accepted only if `failure_code` is `None` and `outcomes` is empty
- AND a `pending` result with a `failure_code` or non-empty `outcomes` SHALL raise `ValueError`

### Requirement: Capture reprocess worker loop

The server SHALL run a same-process asyncio background loop (`_run_capture_reprocess_loop`)
governed by `MEMORY_MCP_CAPTURE_REPROCESS_INTERVAL_SECONDS` (default `5`, `0` disables).
The loop SHALL process up to 20 `PENDING` captures per batch, continue immediately while
`has_more` is true (soft limit 16 consecutive batches, then 1s backoff), and degrade health
to `capture_reprocess.state="degraded"` on exception without propagating to the MCP service.

#### Scenario: Worker drains pending backlog with has_more

- GIVEN more than `batch_limit` `PENDING` rows exist
- WHEN the worker runs one batch
- THEN it SHALL process exactly `batch_limit` rows and report `has_more=true`

#### Scenario: Worker degrades health on exception

- WHEN the worker operation raises an exception
- THEN the loop SHALL log the error and set `capture_reprocess.state` to `degraded`
- AND SHALL NOT propagate the exception to the MCP service

### Requirement: Capture enqueue persistence

The `memory_captures` table SHALL store `content` (NOT NULL, redacted source text) and
`subject_hint` (nullable, redacted) columns so the worker can reconstruct the `TurnEnvelope`
without joining outcome tables. A partial index `memory_captures_pending_idx` SHALL exist on
`memory_captures (created_at) WHERE status = 'pending'` for worker pickup efficiency.

#### Scenario: Pending row stores redacted content

- WHEN `enqueue_capture` writes a `PENDING` row
- THEN the `content` column SHALL hold the `sensitive_guard.inspect`-redacted text
- AND `subject_hint` SHALL hold the redacted hint or `NULL`

#### Scenario: Pending index enables worker pickup

- WHEN the schema is migrated
- THEN the `memory_captures_pending_idx` partial index SHALL exist
- AND `schema.validate_schema` SHALL list it in `_REQUIRED_INDEXES`

### Requirement: Hook-forced capture enqueue

The Agent Stop hook SHALL call `capture_completed_turn` (enqueue path) on every
top-level turn that produces a non-empty `final_output`, rather than relying on
model-autonomous judgment. The hook SHALL skip enqueue for turns whose assistant
output only invoked memory *management* tools (`search_memories`, `list_memories`,
`get_memory`, `get_memory_stats`, `revoke_memory`, `link_memories`,
`revoke_memory_relation`, `list_pending_reviews`, `confirm_pending_memory`,
`reject_pending_memory`, `batch_confirm_pending`) or memory *inspection* tools.
Enqueue failure SHALL fail open (warn, never raise) and SHALL NOT write an outbox
retry record; the next Stop with the same `conversation_id`+`turn_id` re-enqueues
and server-side `event_id` idempotency absorbs the replay.

#### Scenario: Stop hook enqueues every business turn

- GIVEN an Agent Stop event with a non-empty `final_output` and no
  memory-management tool usage
- WHEN the Stop hook runs
- THEN the hook SHALL call `capture_completed_turn` with `status="pending"`
- AND SHALL delete the saved turn state for that turn

#### Scenario: Inspect or manage turn skips enqueue

- GIVEN an Agent Stop event whose assistant output invoked only memory
  management/inspection tools
- WHEN the Stop hook runs
- THEN the hook SHALL NOT call `capture_completed_turn`
- AND SHALL return a skip outcome (`inspect_or_manage_turn`)

#### Scenario: Enqueue transport failure fails open

- WHEN `capture_completed_turn` raises a transport error and `fail_open` is enabled
- THEN the hook SHALL log a warning and return a fail-open outcome
- AND SHALL NOT retry, write an outbox, or propagate the exception

#### Scenario: Turn state bridges missing user_input in Stop event

- GIVEN the BeforeRun hook saved the turn's user_input as turn state
- WHEN the Stop hook runs
- THEN the hook SHALL load the saved turn state to supply `user_input`
- AND SHALL delete the turn state after enqueue (or skip) completes
