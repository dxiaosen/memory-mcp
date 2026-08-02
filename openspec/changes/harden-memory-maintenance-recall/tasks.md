## 1. Core contracts

- [x] 1.1 Add the expired review invariant and bounded maintenance result contract.
- [x] 1.2 Extend the repository port with system maintenance and hybrid recall candidate operations.
- [x] 1.3 Add the maintenance application service and expose it only through the internal composition boundary.

## 2. Persistence and retrieval adapters

- [x] 2.1 Add migration 0009 for `pg_trgm`, partial recall indexes, review status and maintenance indexes.
- [x] 2.2 Implement one-transaction PostgreSQL maintenance with row locking, idempotent transitions and relation staleness.
- [x] 2.3 Implement owner/Profile-first PostgreSQL lexical/recent candidate retrieval with a strict total limit.
- [x] 2.4 Implement equivalent InMemory maintenance and hybrid candidate behavior.

## 3. Runtime integration

- [x] 3.1 Add the bounded maintenance interval setting and document fixed batch/retention policies.
- [x] 3.2 Add the lifespan maintenance runner with thread offload, fast continuation, failure isolation and graceful shutdown.
- [x] 3.3 Route RecallService through hybrid candidates and add content-safe retrieval/maintenance telemetry.

## 4. Verification

- [x] 4.1 Add focused Core tests for expiration, review termination, relation staleness, idempotency and concurrency-safe conditions.
- [x] 4.2 Add recall tests for older lexical matches, overlap deduplication, limit enforcement and owner/Profile isolation.
- [x] 4.3 Add PostgreSQL migration/contract and Server lifespan/configuration tests.
- [x] 4.4 Run the focused and full test suites, lint, format, wheel builds, migration health and strict OpenSpec validation.

## 5. Documentation and evaluation

- [x] 5.1 Update design, configuration, deployment, usage, testing and logging documentation without changing Agent setup.
- [x] 5.2 Extend the investment recall evaluation with an older durable-memory case and record before/after evidence.
- [x] 5.3 Review the final project structure, configuration surface, logs, migrations and docs for conflicts or redundant test-only artifacts.
