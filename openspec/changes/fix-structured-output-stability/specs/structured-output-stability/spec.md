## ADDED Requirements

### Requirement: Candidate structured output is canonicalized before validation

The candidate backend SHALL apply `normalize_candidate_batch_output` to the model's raw structured output before `CandidateBatch.model_validate`. A single-level double-wrapper `{"candidates": {"candidates": [...]}}` SHALL be unwrapped to `{"candidates": [...]}`. A legitimate empty batch `{"candidates": []}` and a normal `{"candidates": [{...}]}` SHALL pass through unchanged. `None`, a non-object, or an otherwise invalid schema SHALL raise `InvalidModelOutputError` (retryable). Canonicalization SHALL NOT recurse, guess schema, or repair arbitrary JSON; only one explicit double-wrapper is unwrapped.

#### Scenario: Double-wrapper is unwrapped

- **WHEN** the model returns `{"candidates": {"candidates": [{...}]}}`
- **THEN** it is normalized to `{"candidates": [{...}]}` and validated successfully

#### Scenario: Legitimate empty batch succeeds

- **WHEN** the model returns `{"candidates": []}`
- **THEN** the backend returns an empty candidate list and the capture completes (not `invalid_candidate_output`)

#### Scenario: None output is rejected as retryable

- **WHEN** the model returns `None`
- **THEN** `normalize_candidate_batch_output` raises `InvalidModelOutputError`

### Requirement: Structured output failures record raw diagnostics

When candidate structured-output validation fails, the extractor SHALL emit a `memory.capture.structured_output.invalid` content event (dev content-log gated) including `model_id`, `prompt_version`, `schema_version`, `raw_type`, `raw_preview` (truncated raw response), `error_type`, and `error_message`. The diagnostics SHALL be attached to the `InvalidModelOutputError.context` so the bounded retry and `memory.capture.incomplete` paths can surface the real failure layer (None / schema malformed / double-wrapper).

#### Scenario: None failure records raw type

- **WHEN** the model returns `None` and validation fails
- **THEN** the raised `InvalidModelOutputError.context` records `raw_type="NoneType"` and the `structured_output.invalid` event is emitted with `model_id`
