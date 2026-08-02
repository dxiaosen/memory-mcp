## ADDED Requirements

### Requirement: Capture responses preserve terminal semantics
The Agent SHALL distinguish `completed`, `failed`, `reprocess_required` and transport/tool warnings. It MUST delete local payload state only after `completed` or a permanent `failed` result. It MUST retain the payload and emit a stable warning after `reprocess_required` or a retryable client failure.

#### Scenario: Server requests reprocessing
- **WHEN** `capture_completed_turn` returns `reprocess_required` with a failure code
- **THEN** the Hook preserves the exact prompt, final output and observed time and returns a content-free warning

#### Scenario: Permanent model output failure
- **WHEN** `capture_completed_turn` returns `failed`
- **THEN** the Hook reports a permanent capture warning and removes the payload because replay cannot change that terminal result

### Requirement: Retried payload identity is stable
The Agent MUST persist the first capture `observed_at` before network I/O and MUST reuse it with the same event ID, conversation ID, turn ID, prompt and final output for every retry. Local state files MUST remain owner-local to the Host, use restrictive permissions and never enter operational logs.

#### Scenario: Process exits after uncertain delivery
- **WHEN** the Hook process exits after a retryable timeout and a later Hook invocation retries the same payload
- **THEN** the canonical payload fingerprint is unchanged and the Server can replay or reprocess the logical event without an idempotency conflict

### Requirement: Local pending delivery is bounded
The command Hook SHALL retry at most one older pending capture during a later Stop invocation, SHALL retain at most the existing TTL-bounded state set, and SHALL NOT require an external queue, daemon, Agent identifier or additional user configuration.

#### Scenario: Pending delivery remains unavailable
- **WHEN** an older pending capture retry fails while a new Stop event is handled
- **THEN** the older payload remains stored, the new event is still processed, and no business content is emitted in logs or warnings

### Requirement: Cross-process capture has one authoritative commit
For the same owner and logical event, PostgreSQL SHALL commit memory, review, Evidence, relation and outcome writes at most once. If two Service instances process an identical fingerprint concurrently, the later commit SHALL return the stored authoritative result with `replayed=true`; a different fingerprint MUST return `idempotency_conflict`.

#### Scenario: Two services overlap with identical payload
- **WHEN** two Service instances complete extraction for the same owner/event/fingerprint before either caller receives a receipt
- **THEN** exactly one capture write set exists and both callers receive the same capture ID

#### Scenario: Two services overlap with different payload
- **WHEN** two Service instances use the same owner/event with different fingerprints
- **THEN** one logical event wins and the other caller receives `idempotency_conflict` without writing candidates

