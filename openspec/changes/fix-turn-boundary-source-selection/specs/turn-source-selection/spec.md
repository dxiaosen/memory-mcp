## ADDED Requirements

### Requirement: CompletedTurnEvent contains only the current turn

The Agent Host Adapter SHALL bound each `capture_completed_turn` to the current turn: only the current user prompt, the tool calls/tool results produced after that prompt, and the current assistant response SHALL be sent. The Server SHALL NOT depend on Claude Code transcript structure. `extract_document_messages` SHALL accept a `user_prompt` and slice the transcript to entries after the most recent user text message matching it (falling back to the last user text message, then to all entries when no user text message exists). A turn with no tool calls SHALL yield zero document messages.

#### Scenario: Second turn excludes first turn tool messages

- **WHEN** a transcript contains two turns where turn 1 reads fileA and turn 2 has no tool calls, and turn 2's Stop hook captures with `user_prompt` set to turn 2's prompt
- **THEN** turn 2's `document_messages` is empty and does not include turn 1's fileA document

#### Scenario: Turn with no tool calls yields empty documents

- **WHEN** the current turn's user prompt is followed only by an assistant response (no Read tool calls)
- **THEN** `extract_document_messages` returns an empty list

### Requirement: source_expression matching tolerates only whitespace differences

Candidate source_expression validation and source message location SHALL compare `normalize(source_expression) in normalize(source)`, where `normalize` collapses any run of whitespace (including `\r\n`, `\r`, `\n`) to a single space and trims leading/trailing whitespace. Normalization SHALL NOT apply NFKC, casefolding, or any character rewrite. A source_expression that is a real contiguous span differing only by whitespace SHALL be accepted; a source_expression splicing multiple independent bullets (dropping bullet markers) SHALL remain `invalid_source_expression`.

#### Scenario: Wrapped-line source_expression is accepted

- **WHEN** the source contains a sentence wrapped across a newline (`优化\n保持`) and a proposal's `source_expression` uses a space (`优化 保持`)
- **THEN** the proposal passes source_expression validation and is not discarded

#### Scenario: Spliced bullet source_expression stays invalid

- **WHEN** the source has two independent bullets `-二期产线...62%；` and `-综合良率...85%；` and a proposal's `source_expression` joins them dropping the second bullet marker (`62%；综合良率`)
- **THEN** the proposal is discarded with reason_code `invalid_source_expression`

### Requirement: User original expression is preferred over assistant paraphrase

When a source_expression matches multiple messages, source binding priority SHALL be `user > tool > assistant`. The extraction prompt SHALL instruct that for the user's own view, preference, decision, risk, thesis, or long-term research baseline, `source_expression` is set to the user's exact original words, not the assistant's paraphrase. A user-sourced candidate with `assertion_kind=user_view`, `expression_basis=explicit`, and sufficient confidence SHALL be auto-saved (not downgraded by `non_user_source`).

#### Scenario: User thesis binds to user and auto-saves

- **WHEN** the same expression appears in both a user message and an assistant message, and a proposal uses that expression with `assertion_kind=user_view`, `expression_basis=explicit`, high confidence
- **THEN** the persisted candidate's evidence `source_role` is `user`, `assertion_kind` is `user_view`, and the decision is `auto_save`

### Requirement: Team extraction deduplicates by team owner

`TeamExtractionService` SHALL deduplicate `team_configs` by `team_owner_id` before batch execution, merging `member_owner_ids` of the same team into a union (order-preserving) and keeping the first `profile_id`. A team owner appearing N times in the config SHALL be processed exactly once per `run_once`.

#### Scenario: Duplicate team owner configs collapse to one run

- **WHEN** three team configs share the same `team_owner_id` with different member subsets, and the unioned members hold similar memories
- **THEN** `run_once` returns exactly one result for that team owner, and the members are the union (so the cluster meets `min_cluster_size`)
