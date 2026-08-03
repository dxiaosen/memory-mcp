## ADDED Requirements

### Requirement: Sensitive content rules are deployment-configurable
The Server SHALL allow sensitive detection rules to be injected from deployment configuration without code changes. When no rules are configured, the Server SHALL use the default rule set. Configured rules SHALL be validated at startup; invalid regex patterns SHALL fail closed with a stable error before any memory is captured.

#### Scenario: Custom rules override defaults
- **WHEN** the deployment configures a custom sensitive rules JSON array
- **THEN** the guard uses only the configured rules and does not apply default rules

#### Scenario: No configuration falls back to defaults
- **WHEN** the deployment does not configure sensitive rules
- **THEN** the guard uses the default rule set and existing safety benchmarks pass

#### Scenario: Invalid regex is rejected at startup
- **WHEN** a configured rule pattern is not a valid regex
- **THEN** the Server raises a `ValueError` at configuration parse time

### Requirement: Tool errors fail fast on unknown exceptions
The MCP tool layer SHALL map only known boundary errors and explicit transient errors (`OSError`, `TimeoutError`) as retryable. Unknown exceptions SHALL be reported as non-retryable and logged at ERROR level, so that programming errors are not retried as temporary failures.

#### Scenario: Unknown programming error
- **WHEN** a tool handler raises a `TypeError` or `AttributeError`
- **THEN** the error response is non-retryable and the operational log records it at ERROR level

#### Scenario: Transient network error
- **WHEN** a tool handler raises an `OSError`
- **THEN** the error response is retryable

### Requirement: Maintenance loop backs off on sustained backlog
The maintenance runner SHALL continue processing consecutive `has_more` batches without waiting for the full interval, but after a soft limit of consecutive `has_more` batches it SHALL insert a short backoff delay to avoid a tight loop that continuously occupies a database connection. Any `has_more=False` result or failure SHALL reset the consecutive counter.

#### Scenario: Sustained backlog triggers backoff
- **WHEN` a maintenance operation returns `has_more=True` more than the soft limit consecutive times
- **THEN** the runner inserts a short backoff delay before the next batch

#### Scenario: Backlog clears resets counter
- **WHEN** a batch returns `has_more=False` after a sustained backlog
- **THEN** the consecutive counter resets and the runner waits for the full interval
