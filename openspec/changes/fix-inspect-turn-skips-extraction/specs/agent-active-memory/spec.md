## MODIFIED Requirements

### Requirement: Capture the completed top-level turn
On a normalized `after_run` event from `Stop` or canonical `AfterRun`, the adapter MUST
use the normalized conversation and turn identifiers to load the matching original user
input and combine it with the final output. It SHALL call `capture_completed_turn` only
when both messages form a successfully completed top-level turn, AND the turn is not an
inspect/manage turn (a turn whose assistant invoked a memory management tool to view or
operate on already-stored memories). It SHALL return a non-continuing successful outcome.

For inspect/manage turns, the adapter SHALL skip `capture_completed_turn` entirely and
return a non-continuing diagnostic outcome (`inspect_or_manage_turn`). Such turns carry
no extractable business facts—running extraction only wastes model calls and always
fails relation provenance validation because the user's inspect request text contains no
`source_expression` fragments.

An inspect/manage turn is detected by a structural signal: the current turn's
transcript contains an assistant `tool_use` block whose tool name is one of the memory
management tools (`search_memories`, `list_memories`, `get_memory`, `get_memory_stats`,
`revoke_memory`, `link_memories`, `revoke_memory_relation`, `list_pending_reviews`,
`confirm_pending_memory`, `reject_pending_memory`, `batch_confirm_pending`).
`recall_memory` is excluded from this set because the BeforeRun hook invokes it
automatically on every business turn. When no transcript is available (generic contract
/ non-Claude-Code hosts) or transcript parsing fails, the adapter SHALL NOT skip
capture (degrade to existing behavior—prefer a wasted extraction over dropping a
business turn).

#### Scenario: Top-level response completes
- **WHEN** `Stop` contains a non-empty final assistant message and matching saved user prompt
- **AND** the turn is not an inspect/manage turn
- **THEN** one versioned completed-turn event is submitted with role-labeled user and assistant messages
- **AND** the stable event identifier supports idempotent retry

#### Scenario: Turn is interrupted or has no final response
- **WHEN** no `Stop` event occurs or `last_assistant_message` is absent
- **THEN** the command does not submit an incomplete completed-turn event

#### Scenario: Hook is enabled after a turn began
- **WHEN** `Stop` has no matching saved prompt state
- **THEN** the command fails open with a non-content diagnostic
- **AND** it does not parse the host transcript or invent user evidence

#### Scenario: Inspect or manage turn skips capture
- **WHEN** `Stop` has a non-empty final assistant message and matching saved user prompt
- **AND** the current turn's transcript contains an assistant `tool_use` block invoking a memory management tool (e.g. `search_memories`, `revoke_memory`, `confirm_pending_memory`)
- **THEN** the adapter does not call `capture_completed_turn`
- **AND** it returns a diagnostic outcome with `inspect_or_manage_turn`

#### Scenario: Business turn with only recall_memory still captures
- **WHEN** the current turn's transcript contains only `recall_memory` and/or non-memory tools (e.g. `Read`, `Bash`)
- **THEN** the adapter calls `capture_completed_turn` normally (not skipped)

#### Scenario: No transcript degrades to capture
- **WHEN** no `transcript_path` is available or transcript parsing fails
- **THEN** the adapter does not skip capture and proceeds with existing behavior
