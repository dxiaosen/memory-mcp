## ADDED Requirements

### Requirement: Relation extraction retries recoverable failures with three-level source_expression validation

The relation planner SHALL wrap extraction and admission in a bounded retry loop (`max_attempts=3`). Relation `source_expression` validation SHALL use three-level whitespace normalization containment (raw -> `normalize_whitespace` -> `normalize_compact`), removing only Unicode whitespace and not rewriting punctuation/digits/characters. The admission step SHALL collect rejected proposals (reason_code `invalid_source_expression`/`relation_endpoint_outside_catalog`/`relation_policy_mismatch`) instead of raising on the first invalid one, and log a `memory.capture.relation_validation_rejected` content event with each rejected proposal's `source_memory_id`/`target_memory_id`/`relation_type`/`confidence`/`source_expression`/`reason_code`. Each attempt SHALL log `memory.capture.relation_extraction_attempt.started`/`.failed`/`.completed` with `capture_id`, `attempt`, `max_attempts`, `duration_ms`, `error_type`, `retryable`. When an attempt has no rejected proposals it SHALL succeed and return accepted relations. Only when all attempts have rejected proposals SHALL the planner raise `InvalidModelOutputError`, causing the capture to fail atomically (`incomplete` + `invalid_candidate_output`) -- invalid relations SHALL NOT be silently ignored. Retry SHALL NOT produce duplicate Capture or Memory writes.

#### Scenario: First attempt invalid, second valid

- **WHEN** the relation extractor returns a proposal with a source_expression not in the source on attempt 1, and a valid proposal on attempt 2
- **THEN** the capture completes, the extractor is invoked exactly twice, and exactly one relation is written

#### Scenario: All attempts invalid

- **WHEN** every attempt produces a proposal with an invalid source_expression
- **THEN** the capture is `FAILED` with `failure_code=invalid_candidate_output`, the extractor is invoked exactly `max_attempts` times, and no memory or relation is persisted

#### Scenario: Whitespace-only difference is accepted

- **WHEN** a relation source_expression differs from the source only by a deleted newline
- **THEN** the proposal passes validation (compact level matches) and is not rejected

### Requirement: Operational instructions are discarded

Candidate processing SHALL discard a proposal whose source_expression or content is an operational instruction (do not use tools / read files / go online / open files) with reason_code `operational_instruction`, unless the user explicitly expresses a cross-session durable preference (e.g. "from now on all sessions" / "以后所有会话"). The check SHALL be memory-type agnostic and SHALL NOT hardcode `research_preference` in Core. Operational instructions SHALL NOT be auto-saved or create Pending reviews.

#### Scenario: Operational instruction discarded

- **WHEN** a candidate's source_expression is `不要使用内置的记忆工具` and no explicit durable preference is expressed
- **THEN** the candidate is discarded with `operational_instruction` and no memory is written

#### Scenario: Explicit durable preference kept

- **WHEN** a candidate's source_expression is `以后分析公司时使用中文`
- **THEN** the candidate is not discarded as operational and proceeds to normal admission

### Requirement: Recall query normalization preserves entities and skips operational-only queries

Recall query normalization SHALL split the query into clauses (on sentence punctuation and newlines) and drop only pure operational instruction clauses (do not use tools / read files / go online, file-list lines, "please read", "output a ...", "by format"). Clauses bearing entities or research-task keywords (e.g. "请基于此前研究判断") SHALL be preserved. When all clauses are dropped, normalization SHALL return an empty string. When the normalized query is empty and no subject/task_intent is present, Recall SHALL skip semantic recall (no embedding call, no candidate search) and return an empty result (`result_count=0`, `rendered_context=""`), logging `memory.recall.query_skipped_operational`. The global relevance threshold SHALL NOT be lowered.

#### Scenario: Operational-only query skips recall

- **WHEN** the query is `不要使用任何内置工具` and no subject/task_intent is set
- **THEN** Recall returns zero results, empty rendered context, and the embedding provider is not called

#### Scenario: Entity-bearing clause preserved

- **WHEN** the query is `我要准备启明先进材料下一次财报跟踪，请基于此前研究判断列出最值得验证的问题。`
- **THEN** the normalized query keeps `启明先进材料`/`财报跟踪`/`研究判断`

### Requirement: Recall completed records accounted and unaccounted duration

The `memory.recall.completed` event SHALL include `accounted_duration_ms` (sum of stage durations: query_embedding + repository_candidate + ranking + evidence_loading + render) and `unaccounted_duration_ms` (total duration minus accounted, clamped at 0), to surface time not explained by measured stages. The Recall architecture SHALL NOT change.

#### Scenario: Accounted and unaccounted recorded

- **WHEN** a recall completes
- **THEN** `memory.recall.completed` records both `accounted_duration_ms` and `unaccounted_duration_ms` as non-negative values
