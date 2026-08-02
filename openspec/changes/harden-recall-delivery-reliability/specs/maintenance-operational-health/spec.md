## ADDED Requirements

### Requirement: Maintenance runtime health is observable
The Server SHALL track whether maintenance is disabled, starting, healthy or degraded. It SHALL expose the last successful effective time, last failure time, consecutive failure count and safe error type without exposing owner identifiers, query text, memory content, Evidence, credentials or exception messages.

#### Scenario: Maintenance succeeds
- **WHEN** a maintenance batch completes after zero or more failures
- **THEN** health reports `ok`, records the successful effective time and resets consecutive failures to zero

#### Scenario: Maintenance fails
- **WHEN** the maintenance operation raises an exception while PostgreSQL health checks still succeed
- **THEN** MCP remains available, health reports a degraded maintenance substate, and operational logs contain only safe failure metadata

### Requirement: Expiration safety does not depend on runner health
All read and recall paths MUST continue to exclude expired-by-time revisions and relations even when the maintenance runner is disabled or degraded.

#### Scenario: Runner is degraded before materialization
- **WHEN** a current active revision has passed `valid_until` but has not yet been materialized as expired
- **THEN** list, get-active, relation-active and recall operations exclude it

