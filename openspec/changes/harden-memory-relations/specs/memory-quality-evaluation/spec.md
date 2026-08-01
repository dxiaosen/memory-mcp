## ADDED Requirements

### Requirement: Memory quality evaluation is repeatable and isolated
The project SHALL provide a versioned, Secret-free evaluation dataset and runner for candidate extraction, relationship admission, recall relevance, and safety boundaries. Offline evaluation MUST NOT access the network, production PostgreSQL, runtime Tokens, or owner data.

#### Scenario: Offline deterministic tasks run in CI
- **WHEN** the evaluation runner is invoked in offline mode
- **THEN** it validates all cases, scores recall and safety, marks candidate/relation as not evaluated, and exits non-zero when an evaluated quality threshold is missed

#### Scenario: Invalid evaluation data fails safely
- **WHEN** a case contains an unknown task, missing expected result, duplicate case ID, or identity/Secret field
- **THEN** validation fails before any evaluator or model is called

### Requirement: Evaluation reports decision-relevant metrics
The runner SHALL report Recall@K and safety-negative pass rate, plus candidate auto-save precision/recall and automatic relation precision/recall when live predictions are explicitly requested. It SHALL include case counts and dataset version. Metric computation MUST be deterministic for identical predictions.

#### Scenario: False positive affects precision
- **WHEN** a prediction saves or links an item that the case marks negative
- **THEN** the relevant precision metric decreases and the false positive is counted

#### Scenario: Paraphrased recall target is measured
- **WHEN** a recall case identifies one or more expected labels
- **THEN** Recall@K is computed from label membership rather than exact generated prose

### Requirement: Live model evaluation is explicit
External model evaluation MUST require an explicit live-model option, reuse the existing `MEMORY_MCP_MODEL_*` configuration, avoid production database writes, and return only case IDs plus aggregate metrics by default.

#### Scenario: Default invocation cannot call a provider
- **WHEN** the runner is invoked without the live-model option
- **THEN** no configured or ambient model endpoint is contacted

#### Scenario: Live model configuration is invalid
- **WHEN** live evaluation is explicitly requested without a valid configured model
- **THEN** startup fails with a configuration error before evaluating cases
