## ADDED Requirements

### Requirement: Investment benchmark covers decision-relevant memory behavior
The project SHALL provide a versioned Chinese investment-research benchmark covering all configured investment memory types, all configured directed relation types, semantic recall with plausible distractors, and financial safety boundaries. Every case MUST have a stable ID, task, category, expected result and no identity or Secret field.

#### Scenario: Investment dataset is complete
- **WHEN** the benchmark dataset is validated
- **THEN** it contains positive and negative candidate/relation cases plus recall and safety cases covering theses, evidence, risks, catalysts, questions, ongoing research, decisions and research preferences

#### Scenario: Identity field is inserted
- **WHEN** a case contains owner, tenant, Token or another unsupported field
- **THEN** strict validation fails before a model or evaluator is called

### Requirement: Live model evaluation is explicit and isolated
The benchmark MUST require `--live-model` before reading `MEMORY_MCP_MODEL_*` or calling a provider. It MUST reuse the current candidate/relation extraction and trusted admission boundaries with an in-process repository, MUST NOT connect to PostgreSQL, and MUST identify recall and safety as deterministic tasks rather than model results.

#### Scenario: Default benchmark runs in CI
- **WHEN** the runner is invoked without `--live-model`
- **THEN** it evaluates recall and safety without constructing a provider client, reading runtime owner data or accessing a database, and marks candidate/relation as not evaluated rather than replaying gold labels

#### Scenario: Configured model benchmark runs
- **WHEN** `--live-model` is supplied with a valid model configuration
- **THEN** candidate and relation predictions use that model while recall and safety continue using the production deterministic implementations

### Requirement: Benchmark reports are reproducible and safe to retain
Each report SHALL include dataset version/hash, mode, safe model/prompt/schema identifiers, UTC run time, duration, task counts, category counts/pass rates, aggregate metrics and failed case IDs. Reports MUST NOT contain case content, owner/tenant/client identifiers, database URLs, Bearer Tokens, API keys or provider exception text.

#### Scenario: A report is written to disk
- **WHEN** the operator supplies an existing-parent `--output` path and evaluation completes
- **THEN** the runner writes only the safe JSON report and returns non-zero if configured thresholds are missed

#### Scenario: Output parent is missing
- **WHEN** the operator supplies an output path whose parent directory does not exist
- **THEN** the runner fails without creating directories or calling a model

### Requirement: Real benchmark evidence is documented without overstating it
The project SHALL record the command boundary, dataset version, safe model ID, aggregate/category results, failed case IDs and limitations of a completed live run in reader documentation. The document MUST distinguish a single benchmark snapshot from statistical significance, factual verification, investment advice or production SLA evidence.

#### Scenario: Reader reviews the benchmark result
- **WHEN** a reader opens the evaluation document
- **THEN** they can determine what used the model, what remained deterministic, which categories failed and what conclusions the run does not support

### Requirement: Relation admission rejects negated or reversed evidence
The relation extraction path SHALL require a cited expression with recognizable source and target endpoint evidence, and SHALL reject proposals whose expression is only a relation cue or explicitly negates the proposed relation. Profiles MAY declare direction cues for a relation; when both endpoint references around a cue provide clearly stronger reverse evidence, admission SHALL reject the proposal. The prompt SHALL prohibit reversing endpoints or reinterpreting text solely to fit the allowed relation direction. This behavior MUST be generic and MUST NOT depend on benchmark case IDs or investment entities.

#### Scenario: Model cites only a relation verb
- **WHEN** a proposal's source expression is only “supports” or another cue without recognizable source and target evidence
- **THEN** the proposal is not admitted

#### Scenario: Source explicitly denies support
- **WHEN** a proposal cites an expression that states one memory does not support another
- **THEN** the relation is not admitted

#### Scenario: Text expresses only an invalid reverse direction
- **WHEN** the source text expresses a relation only in the reverse of an allowed endpoint direction
- **THEN** the extractor or cue-aware admission returns no relation rather than swapping the endpoints

### Requirement: Recall semantics are profile-driven
Each memory profile MAY declare normalized semantic query hints by memory type. Recall SHALL apply these hints through a generic bounded score contribution while continuing to consider textual relevance, confidence, priority and time. Core MUST NOT contain investment-specific memory type names.

#### Scenario: Query asks for ongoing follow-up
- **WHEN** an investment profile query asks for next steps or follow-up research
- **THEN** ongoing research memories receive the configured semantic contribution without hard-coded investment branching in Core

#### Scenario: Query asks for a settled scope decision
- **WHEN** an investment profile query asks what was finally decided
- **THEN** research decision memories receive the configured semantic contribution

### Requirement: Evaluation-only test scaffolding is minimized
Candidate and relation cases MUST NOT contain predictions copied from gold labels, and the production evaluation runner MUST NOT expose a test-only model predictor injection. Automated tests SHALL focus on dataset contracts, isolation, safe reporting and deterministic product behavior; live provider quality SHALL be evidenced by explicit retained benchmark runs.

#### Scenario: Offline report is inspected
- **WHEN** no live model was requested
- **THEN** candidate/relation metrics are null and their categories report zero evaluated cases
