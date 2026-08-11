## MODIFIED Requirements

### Requirement: Capture the completed top-level turn

The `capture_completed_turn` MCP tool accepts a simplified input contract: the caller
(an Agent deciding autonomously whether to persist a turn) passes `conversation_id`,
`turn_id`, `user_input`, and `final_output`, plus optional `profile_id` and
`subject_hint`. The tool SHALL NOT accept `event_id`, `contract_version`, `observed_at`,
or `messages` from the caller—the server assembles these to eliminate caller-controlled
identity collision or drift.

The server SHALL derive `event_id` deterministically from `(owner_id, conversation_id,
turn_id)` as `memory-agent:{sha256(...)}`, so repeated calls for the same owner+conversation+
turn produce the same `event_id` and the server can replay idempotently. The server SHALL
set `observed_at` to its own clock, `contract_version` to the hardcoded constant `"1"`,
and `payload_fingerprint` to a SHA-256 of the simplified input
(`conversation_id`, `turn_id`, `user_input`, `final_output`, `subject_hint`). When a
pre-existing capture has the same `event_id` but a different `payload_fingerprint`, the
server SHALL reject with `idempotency_conflict`.

The server SHALL assemble `messages` as two entries (`[user, assistant]`) from
`user_input` and `final_output`, and assemble `content` as
`[user]\n{user_input}\n\n[assistant]\n{final_output}`. Document provenance messages are
not assembled in Phase 1 (known degradation—`EvidenceSourceType.DOCUMENT` evidence is
not produced by this path; to be restored in a later phase).

The tool description SHALL guide the caller's gating decision: call only after a turn
where the user stated or revised a durable fact, preference, decision, thesis, or
research judgment; do not call for turns that only inspect/query/search/manage existing
memories or for casual/operational conversation with no lasting signal.

#### Scenario: Simplified input assembles a valid envelope
- **WHEN** the caller passes `conversation_id`, `turn_id`, `user_input`, `final_output`
- **THEN** the server derives `event_id` from `(owner_id, conversation_id, turn_id)`
- **AND** sets `observed_at` to the server clock, `contract_version` to `"1"`
- **AND** computes `payload_fingerprint` over the simplified input
- **AND** assembles `messages` as `[user, assistant]` and `content` as the bracketed join

#### Scenario: Repeated call replays idempotently
- **WHEN** the same `(owner_id, conversation_id, turn_id)` is called again with identical input
- **THEN** the derived `event_id` matches the existing capture
- **AND** `payload_fingerprint` matches
- **AND** the server returns `replayed: true` without re-processing

#### Scenario: Same event_id with different payload is rejected
- **WHEN** the same `(owner_id, conversation_id, turn_id)` is called with different `final_output`
- **THEN** `payload_fingerprint` differs
- **AND** the server returns `error_code: idempotency_conflict`

#### Scenario: Caller-supplied identity fields are rejected
- **WHEN** the caller passes `event_id`, `contract_version`, `observed_at`, or `messages`
- **THEN** the tool rejects via strict-arguments (`extra: forbid`)

## ADDED Requirements

### Requirement: AfterRun hook phase is a capture no-op

The Agent Host Adapter SHALL NOT trigger `capture_completed_turn` from the `after_run`
(Stop) phase. The AfterRun phase SHALL return a no-op outcome (no warning, no
additional context) without staging, delivering, or retrying any capture payload.

Capture is the Agent model's autonomous decision: it calls `capture_completed_turn`
directly when a turn carries durable signal. The adapter SHALL NOT save turn state for
later capture during `before_run`, and SHALL NOT consume a pending outbox during
`after_run`. Existing local outbox entries clear via the 24h TTL.

The adapter SHALL remove inspect/manage-turn skip logic entirely (the structural
transcript-based detection of memory management tool calls). Since the hook never
captures, the skip decision is moot—the model simply does not call capture on
inspect/manage turns.

#### Scenario: Stop event produces no capture
- **WHEN** a `Stop`/`AfterRun` event is handled by the adapter
- **THEN** the outcome is a no-op (no warning_code, no additional_context)
- **AND** no `capture_completed_turn` call is made by the adapter

#### Scenario: BeforeRun recall is unchanged
- **WHEN** a `UserPromptSubmit`/`BeforeRun` event is handled
- **THEN** the adapter recalls and injects memory context as before
- **AND** does not persist turn state for later capture
