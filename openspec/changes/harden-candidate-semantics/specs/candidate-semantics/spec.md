## ADDED Requirements

### Requirement: assertion_kind is normalized against trusted source role and type

Candidate processing SHALL normalize the model-reported `assertion_kind` against the trusted `source_role` and `source_type` (both derived from `turn.messages`, never trusted from model self-report) before constructing the Candidate. A `source_type` of `tool`, `document`, or `web` with a reported kind of `user_view`, `user_provided_fact`, or `system_inference` SHALL be corrected to `external_fact`. A `source_role` of `assistant` with a reported kind of `user_view` or `user_provided_fact` SHALL be corrected to `system_inference`. User-originated candidates SHALL NOT be normalized. When a correction is applied, a DEBUG `memory.capture.candidate.assertion_normalized` event SHALL be emitted with `from_assertion_kind` and `to_assertion_kind`.

#### Scenario: Assistant thesis mislabeled as user_view

- **WHEN** the model returns a candidate sourced from an assistant message with `assertion_kind=user_view`
- **THEN** the persisted candidate has `assertion_kind=system_inference` and a `memory.capture.candidate.assertion_normalized` event records `from=user_view` and `to=system_inference`

#### Scenario: Document fact mislabeled as user_provided_fact

- **WHEN** the model returns a candidate sourced from a tool message with `source_type=document` and `assertion_kind=user_provided_fact`
- **THEN** the persisted candidate has `assertion_kind=external_fact`

#### Scenario: User view is not normalized

- **WHEN** the model returns a candidate sourced from a user message with `assertion_kind=user_view`
- **THEN** the persisted candidate retains `assertion_kind=user_view` and no `assertion_normalized` event is emitted

#### Scenario: Confirm review preserves assertion_kind

- **WHEN** a pending `system_inference` candidate is confirmed by the user
- **THEN** the persisted memory retains `assertion_kind=system_inference` and only `verification_status` changes to `user_confirmed`

### Requirement: Candidate quantity is bounded by soft trimming at 12

The extraction prompt SHALL guide the model to produce 5–10 candidates and never exceed 12. The `StructuredCandidateExtractor.extract` SHALL, after parsing, trim proposals exceeding 12 by descending `confidence` and keep only the top 12, emitting a DEBUG `memory.capture.candidates_truncated` event with `original_count`, `kept_count`, and `soft_limit`. The hard schema cap `MAX_CANDIDATES` SHALL remain at 20 so that 13–20 candidates are trimmed rather than failing the entire round.

#### Scenario: Fifteen candidates trimmed to twelve

- **WHEN** the model returns 15 valid candidates
- **THEN** `extract` returns 12 proposals sorted by descending confidence, and a `candidates_truncated` event records `original_count=15` and `kept_count=12`

#### Scenario: Twenty candidates still parse (hard cap not lowered)

- **WHEN** the model returns 20 valid candidates
- **THEN** the schema accepts the batch (within `MAX_CANDIDATES=20`) and `extract` trims to 12

### Requirement: File/tool provenance is reconstructed from Claude Code transcript

The Agent Host Adapter SHALL accept a `transcript_path` from Claude Code Stop and UserPromptSubmit hooks, parse the JSONL transcript to reconstruct `Read` tool calls and their results, and emit `role=tool` messages with `source_type=document`, `source_uri`, `source_title`, and `tool_name` into `capture_completed_turn` messages between the user and assistant messages. The parsed document messages SHALL be persisted in the TurnState outbox so redelivery does not depend on the transcript file still existing. Transcript parsing SHALL be best-effort: unreadable, malformed, or empty transcripts return an empty list without blocking capture.

#### Scenario: Read tool call surfaces as document message

- **WHEN** the Stop hook provides a `transcript_path` whose transcript contains a `Read` tool_use with `file_path=/work/04_纪要.md` and a matching `tool_result` with the file content
- **THEN** the `capture_completed_turn` request includes a `document_messages` entry with `source_type=document`, `source_uri=/work/04_纪要.md`, `source_title=04_纪要.md`, and `tool_name=Read`

#### Scenario: Unreadable transcript does not block capture

- **WHEN** the Stop hook provides a `transcript_path` that does not exist or contains malformed JSON
- **THEN** `extract_document_messages` returns an empty list and the capture proceeds with only user and assistant messages

#### Scenario: Failed read (is_error) is skipped

- **WHEN** a `Read` tool_result has `is_error=true`
- **THEN** no document message is produced for that read, but other successful reads are still surfaced

### Requirement: Zero-recall returns empty rendering

When recall yields zero items, the result SHALL return `rendered_context=""` and `estimated_tokens=0`. The Server SHALL NOT inject a placeholder string such as "No relevant historical user context was recalled." into the Agent context.

#### Scenario: Zero results produce empty context

- **WHEN** a recall query matches no memories
- **THEN** `rendered_context` is the empty string and `estimated_tokens` is 0
