## ADDED Requirements

### Requirement: Explicit uncertainty takes priority over explicit durable

The admission policy SHALL check `has_explicit_uncertainty(candidate)` before the `explicit_durable_statement` auto-save path. When the candidate's `source_expression` or `content` matches an explicit uncertainty pattern (just a guess / maybe / perhaps / temporary / uncertain / unverified / not enough evidence / hypothesis / do not treat as confirmed), the candidate SHALL be admitted as PENDING with reason_code `explicit_uncertainty`, even if it is explicit + durable + high-confidence. The uncertainty check SHALL be deterministic (regex over adjacent source text), not semantic. `research_question` and `ongoing_research` SHALL NOT be forced to Pending by this rule--only explicitly uncertain conclusions/hypotheses.

#### Scenario: Explicit guess goes Pending

- **WHEN** a user-sourced candidate says "I guess Q3 NRR might return to 111%, this is just a guess, no sufficient evidence"
- **THEN** the candidate is PENDING with `explicit_uncertainty`, not auto-saved

#### Scenario: Explicit durable thesis still auto-saves

- **WHEN** a user-sourced thesis says "I confirm Q3 NRR has returned to 111%"
- **THEN** the candidate is not flagged uncertain and proceeds to normal admission (auto-save if durable + high-confidence)

### Requirement: Explicit replacement uses semantic target fallback

When `_is_explicit_replacement(candidate)` is true and the literal subject+type match fails (`find_current` returns no target), the processor SHALL attempt a bounded semantic fallback: query `find_semantically_similar` with a relaxed threshold (0.45) over same owner+profile+type active memories. If a unique clear target is found, it SHALL be used as the replacement target (supersede old revision, new revision active/current). If none or ambiguous, the candidate SHALL go PENDING (`ambiguous_lifecycle_target` or new). The fallback SHALL NOT create duplicate replacement rows.

#### Scenario: Corrected thesis replaces old despite different subject

- **WHEN** a user explicitly corrects an earlier thesis with different subject wording, and a semantically similar old thesis exists
- **THEN** the old thesis is superseded, the new thesis is active/current, and `replacement_count=1`

### Requirement: Tool scopes isolate runtime ingestion from memory management

`link_memories` SHALL require `memory:review` (not `memory:write`), making `memory:write` a runtime-ingestion-only scope (Hook token). A central `TOOL_SCOPES` mapping SHALL be the single source of truth for tool→scope, referenced by both ListTools filtering and CallTool authorization. `MemoryMcpServer.list_tools` SHALL filter visible tools by the principal's scopes (capture_completed_turn invisible to a review-only agent token; memory:review tools invisible to a write-only Hook token). CallTool SHALL still hard-authorize via `require_scope` even when ListTools has filtered. `authorize_tool_call` SHALL reject unknown tool names.

#### Scenario: Write-only token cannot call link_memories

- **WHEN** a principal has only `memory:write` and calls `link_memories`
- **THEN** the call is denied with `permission_denied`

#### Scenario: Review-only agent token does not see capture_completed_turn

- **WHEN** a principal has `memory:read` + `memory:review` and lists tools
- **THEN** `capture_completed_turn` is not in the visible tools
