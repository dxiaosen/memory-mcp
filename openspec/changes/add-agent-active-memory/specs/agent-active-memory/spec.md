## ADDED Requirements

### Requirement: Provide one extensible active-memory Hook command
The system SHALL provide one `memory-mcp-hook` command and one host-independent
top-level turn contract. The command MUST accept current Codex and Claude Code
`UserPromptSubmit`/`Stop` JSON directly, MUST also accept canonical
`BeforeRun`/`AfterRun` events for other command Hook Hosts, and MUST NOT require
host-specific command arguments. Host input normalization, active-memory execution,
and command Hook JSON rendering MUST remain separate boundaries.

#### Scenario: Codex starts and completes a user turn
- **WHEN** Codex invokes the command first with `UserPromptSubmit` and later with `Stop` for the same `session_id` and `turn_id`
- **THEN** the command performs one BeforeRun recall and one AfterRun capture for that top-level turn

#### Scenario: Claude Code starts and completes a user turn
- **WHEN** Claude Code invokes the command first with `UserPromptSubmit` and later with `Stop` for the same `session_id` and `prompt_id`
- **THEN** the command performs the same normalized recall and capture behavior

#### Scenario: Another Agent maps to the canonical contract
- **WHEN** another Agent or thin wrapper submits `BeforeRun` and `AfterRun` with stable conversation and run identifiers
- **THEN** the same active-memory execution runs without changing the MCP Client, Bridge, state store, or Memory Core
- **AND** only a host-specific input or output mapping is required when its native JSON differs

#### Scenario: A subagent finishes
- **WHEN** a host emits `SubagentStop` for an internal operation
- **THEN** the default active-memory integration does not independently capture that subagent result

### Requirement: Keep Agent configuration minimal
An Agent Host SHALL require only `MEMORY_MCP_URL` and `MEMORY_MCP_TOKEN` to use the
default active-memory flow after its static Hook command is registered. Scenario,
recall budget, timeout, retry, state location, owner, client, and Agent identifiers
MUST NOT be required user configuration. Existing
`MEMORY_HOOK_MCP_URL` and `MEMORY_HOOK_BEARER_TOKEN` variables SHALL remain accepted as
compatibility aliases, with the new names taking precedence.

#### Scenario: Agent supplies only address and token
- **WHEN** the Hook process receives valid `MEMORY_MCP_URL` and `MEMORY_MCP_TOKEN`
- **THEN** it uses the built-in `general-work` policy and bounded runtime defaults
- **AND** it completes the active-memory flow without another Agent-specific value

#### Scenario: Authenticated owner is established
- **WHEN** an Agent invokes recall or capture with its configured Token
- **THEN** the server derives the owner from the authenticated Token mapping
- **AND** no Hook input, scenario, session, turn, client, or Agent field can override that owner

#### Scenario: Legacy Agent variables remain deployed
- **WHEN** only the two existing `MEMORY_HOOK_*` connection variables are present
- **THEN** the Hook continues to connect with the same values
- **AND** no Secret value is written to stdout or operational logs

### Requirement: Recall before the model processes each top-level prompt
On a normalized `before_run` event from `UserPromptSubmit` or canonical `BeforeRun`, the
adapter MUST persist the minimal turn-correlation state before attempting recall, then
synchronously call `recall_memory` before the host sends the current prompt to its
model. When current memory is returned, the adapter SHALL return it as host-independent
additional context. The built-in command renderer SHALL emit only valid Hook JSON
containing that context in `hookSpecificOutput.additionalContext`.

#### Scenario: Relevant memory exists
- **WHEN** the authenticated owner has relevant current memory for the submitted prompt
- **THEN** the host model receives the bounded server-rendered historical context with the current prompt
- **AND** stdout contains no diagnostic text outside the Hook JSON object

#### Scenario: No relevant memory exists
- **WHEN** recall returns no eligible relevant items
- **THEN** the command emits an empty successful Hook result
- **AND** the model processes the original prompt without a placeholder memory block

#### Scenario: Recall is temporarily unavailable
- **WHEN** the MCP service times out or returns a retryable recall failure under default fail-open behavior
- **THEN** the current Agent turn proceeds without recalled context
- **AND** no memory from another owner or stale local result is substituted

### Requirement: Capture the completed top-level turn
On a normalized `after_run` event from `Stop` or canonical `AfterRun`, the adapter MUST
use the normalized conversation and turn identifiers to load the matching original user
input and combine it with the final output. It SHALL call `capture_completed_turn` only
when both messages form a successfully completed top-level turn and SHALL return a
non-continuing successful outcome.

#### Scenario: Top-level response completes
- **WHEN** `Stop` contains a non-empty final assistant message and matching saved user prompt
- **THEN** one versioned completed-turn event is submitted with role-labeled user and assistant messages
- **AND** the stable event identifier supports idempotent retry

#### Scenario: Turn is interrupted or has no final response
- **WHEN** no `Stop` event occurs or `last_assistant_message` is absent
- **THEN** the command does not submit an incomplete completed-turn event

#### Scenario: Hook is enabled after a turn began
- **WHEN** `Stop` has no matching saved prompt state
- **THEN** the command fails open with a non-content diagnostic
- **AND** it does not parse the host transcript or invent user evidence

### Requirement: Correlate Hook processes with protected local state
The command SHALL store only the minimum data required to correlate
`UserPromptSubmit` and `Stop`. State paths MUST be derived from a cryptographic digest
of validated identifiers, writes MUST be atomic, directories and files MUST be
restricted to the current OS user, and stale state MUST be removed after a fixed
bounded lifetime. Message content and Secret values MUST NOT enter state filenames or
operational logs.

#### Scenario: Two turns overlap
- **WHEN** distinct turn identifiers in one session are active concurrently
- **THEN** each Stop event loads only its exact corresponding prompt
- **AND** neither turn overwrites or captures the other turn

#### Scenario: Malicious identifier contains path syntax
- **WHEN** a Hook input identifier contains path separators or traversal text
- **THEN** the state store uses only its digest as the filename
- **AND** no file is read or written outside the configured state directory

#### Scenario: Process exits before Stop
- **WHEN** saved state exceeds the fixed expiration time
- **THEN** a later Hook invocation deletes it without logging the stored prompt

### Requirement: Keep general-work as an internal default
The public `recall_memory` and `capture_completed_turn` MCP tools SHALL use
`general-work` when scenario is omitted. Callers MAY explicitly select another
registered scenario for future specialized integrations, but the system MUST NOT infer
or switch scenario from conversation text.

#### Scenario: Direct Agent tool call omits scenario
- **WHEN** an authenticated caller invokes recall or capture without a scenario
- **THEN** the server validates and executes the request under `general-work`

#### Scenario: Caller explicitly selects an unknown scenario
- **WHEN** a caller supplies a scenario that is not registered
- **THEN** the server returns `scenario_not_registered`
- **AND** it does not silently fall back or create memory under another scenario

### Requirement: Preserve fail-open Agent behavior without a queue
The default active-memory command SHALL execute bounded asynchronous MCP I/O inside
each synchronous host Hook process and MUST NOT require an external message queue.
Recall and capture failures MUST NOT block the primary Agent turn under default
fail-open configuration, while logs and optional user-facing diagnostics MUST contain
only stable phases and error codes.

#### Scenario: Capture retry succeeds
- **WHEN** the first capture attempt has an uncertain retryable failure
- **THEN** the command retries the same stable event within the configured bound
- **AND** the server creates at most one logical capture

#### Scenario: All capture retries fail
- **WHEN** bounded capture attempts are exhausted under fail-open behavior
- **THEN** the Agent response remains completed
- **AND** no raw prompt, response, Token, or exception body is emitted as a diagnostic
