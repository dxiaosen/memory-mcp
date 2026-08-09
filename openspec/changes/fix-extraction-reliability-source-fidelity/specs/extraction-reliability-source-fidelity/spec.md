## ADDED Requirements

### Requirement: source_expression matching uses two-level whitespace normalization

Candidate source_expression validation and source message location SHALL compare using two-level whitespace normalization: first `normalize_whitespace` (`" ".join(text.split())`) containment; if that fails, `normalize_compact` (`"".join(text.split())`) containment. Normalization SHALL remove only Unicode whitespace and SHALL NOT rewrite punctuation, digits, or characters (no NFKC/casefold). A source_expression differing only by whitespace (including a deleted newline) SHALL be accepted; a source_expression that rewrites characters, adds punctuation, or splices independent bullets (bullet markers are non-whitespace and survive) SHALL remain `invalid_source_expression`.

#### Scenario: Deleted newline between clauses is accepted

- **WHEN** the source contains `较高毛利率，\n可能` and a proposal's `source_expression` is `较高毛利率，可能` (newline deleted, no space)
- **THEN** the proposal passes validation (compact level matches) and is not discarded

#### Scenario: Spliced bullets stay invalid

- **WHEN** the source has two independent bullets and a proposal's `source_expression` joins them dropping the bullet marker
- **THEN** the proposal is discarded with reason_code `invalid_source_expression`

### Requirement: Candidate extraction prompt enforces atomization and source fidelity

The extraction prompt SHALL require each candidate to be atomic (one fact OR one inference); for `external_fact`/`user_provided_fact`, every key fact and number in content SHALL be fully supported by `source_expression`; multiple table rows, bullets, or sources SHALL be split into multiple candidates; relation semantics (supports/challenges/threatens/could_catalyze/addresses) SHALL NOT be written into fact candidate content. `research_preference` SHALL represent only the user's lasting preferences (source_role=user, expression_basis=explicit); an assistant-proposed framework the user has not adopted SHALL NOT be labeled `research_preference`. For fact candidates, source binding priority SHALL be user explicit statement (user_provided_fact) > tool/document original evidence (external_fact) > assistant paraphrase (system_inference).

#### Scenario: Prompt instructs atomic candidates and source priority

- **WHEN** the system prompt is rendered for a profile
- **THEN** it instructs atomic candidates, full source_expression support for facts, no relation semantics in fact content, research_preference as user-only, and the user>tool>assistant fact source priority

### Requirement: Candidate extraction retries recoverable structure errors

Capture SHALL retry structured candidate extraction on `InvalidModelOutputError` up to a bounded max attempts (3) within the same Capture, logging `memory.capture.extraction_attempt.started`/`.failed`/`.completed` with `capture_id`, `attempt`, `max_attempts`, `duration_ms`, `error_type`, and `retryable`. Business validation failures (e.g. `invalid_source_expression`) SHALL NOT trigger extraction retry. Only after all attempts fail SHALL the capture be written as `incomplete` with `failure_code=invalid_candidate_output`. Retry SHALL NOT produce duplicate Capture or Memory writes.

#### Scenario: First attempt fails, second succeeds

- **WHEN** the extractor raises `InvalidModelOutputError` on the first attempt and succeeds on the second
- **THEN** the capture completes, the extractor is invoked exactly twice, and exactly one memory is written

#### Scenario: All attempts fail

- **WHEN** the extractor raises `InvalidModelOutputError` on every attempt up to the max
- **THEN** the capture is `FAILED` with `failure_code=invalid_candidate_output` and no memory is written

### Requirement: Assistant restatement of existing memory is discarded

When a candidate's trusted `source_role` is `assistant` and it highly duplicates an existing active memory (same subject+type with content equivalence/containment, or semantic similarity above the profile's `semantic_dedup_threshold`), it SHALL be discarded with reason_code `assistant_restatement` and SHALL NOT create a new Pending review or add Evidence to the existing memory. A user's own restatement SHALL continue to follow existing duplicate/evidence rules.

#### Scenario: Assistant echoes an existing memory

- **WHEN** an active memory exists and a later assistant-sourced candidate restates the same content
- **THEN** the candidate is discarded with `assistant_restatement` and no new pending review or memory is created

### Requirement: Recall query is deterministically normalized

Recall SHALL normalize the query by splitting into clauses (on sentence punctuation and newlines) and dropping clauses that are operation/tool/format instructions or file-list lines, keeping entity/topic/research-task clauses, with fallback to the original query when all clauses are dropped. Normalization SHALL be deterministic, SHALL NOT call an LLM, SHALL NOT change owner/profile/lifecycle filtering, and SHALL NOT lower the global relevance threshold. The `memory.recall.input` event SHALL record the normalized query.

#### Scenario: Instruction clauses are stripped, entities kept

- **WHEN** the raw query is a natural-language request with embedded operation/format instructions and file lists
- **THEN** the normalized query keeps entity/topic clauses and drops instruction/file-list clauses

#### Scenario: Pure entity query is unchanged

- **WHEN** the raw query contains only entity keywords with no instruction clauses
- **THEN** the normalized query equals the raw query
