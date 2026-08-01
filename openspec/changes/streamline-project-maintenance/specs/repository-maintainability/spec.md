## ADDED Requirements

### Requirement: Production distributions exclude test-only candidate implementations
The Server distribution MUST contain only runtime candidate/model implementations. Deterministic candidate doubles MUST live under the test boundary and MAY be injected through the existing application port. Production settings MUST NOT expose a fake or fixed backend selector.

#### Scenario: Server wheel is inspected
- **WHEN** the Server wheel is built
- **THEN** it contains no `FixedCandidateBackend`, test fixture payload or import from `tests`

### Requirement: Agent connection configuration has one canonical namespace
The Agent Client SHALL read its remote connection only from `MEMORY_MCP_URL` and `MEMORY_MCP_TOKEN`. It MUST NOT accept the removed `MEMORY_HOOK_MCP_URL` or `MEMORY_HOOK_BEARER_TOKEN` aliases, and it MUST continue to hide the Token from representations and logs.

#### Scenario: Current connection variables are configured
- **WHEN** an Agent process receives valid `MEMORY_MCP_URL` and `MEMORY_MCP_TOKEN`
- **THEN** it connects using those values without requiring owner, client or Agent identity fields

#### Scenario: Only removed aliases are configured
- **WHEN** an Agent process receives only the removed URL/Token aliases
- **THEN** settings validation fails without exposing the supplied Token

### Requirement: Regression tests prioritize product boundaries
The maintained automated suite SHALL cover owner isolation, sensitive data boundaries, idempotency, lifecycle and relation governance, transactionality, PostgreSQL contracts, MCP transport, Agent lifecycle, model schema and package isolation. A test MAY be removed or consolidated only when the same behavior has stronger coverage or when it asserts a deleted test-only implementation.

#### Scenario: Test suite is streamlined
- **WHEN** duplicate and implementation-detail tests are removed
- **THEN** the full remaining suite, offline benchmark and package boundary checks pass without weakening the protected product boundaries

### Requirement: Reader documentation has single-purpose ownership
Current reader documentation SHALL designate one authoritative document for design, runtime configuration, Agent integration, operations, testing, evaluation, logging and deployment. Secondary documents MUST link to the authoritative source instead of copying volatile result tables, configuration matrices or historical acceptance logs.

#### Scenario: Reader follows project navigation
- **WHEN** a reader opens `docs/README.md`
- **THEN** each task is routed to one primary document and OpenSpec history is linked without duplicating every artifact

#### Scenario: Evaluation result is updated
- **WHEN** a new model benchmark snapshot replaces the current result
- **THEN** only `docs/evaluation.md` and the safe result artifact require result updates
