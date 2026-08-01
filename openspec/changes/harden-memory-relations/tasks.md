## 1. Domain and contract

- [x] 1.1 Add relation origin, scope, provenance and stale lifecycle types with strict invariants and public exports
- [x] 1.2 Build manual item-scoped and automatic revision-scoped relationships only from trusted endpoint/Capture metadata
- [x] 1.3 Extend MCP relation DTOs and safe logs without changing tools, Agent configuration or owner boundaries

## 2. Persistence and migration

- [x] 2.1 Add one final `0007` PostgreSQL migration with explicit NULL guards, provenance, revision foreign keys and stale constraints while preserving legacy rows
- [x] 2.2 Update PostgreSQL mapping/write/read/revoke paths and atomically stale revision-scoped relationships during Capture and Review replacement
- [x] 2.3 Update InMemory adapter with matching validation, duplicate, history, rollback and stale transition semantics

## 3. Verification

- [x] 3.1 Add domain/service tests for manual/automatic/legacy invariants, provenance trust and DTO visibility
- [x] 3.2 Add lifecycle/Capture tests for replacement stale, manual survival, new relation recreation, history, owner isolation and rollback
- [x] 3.3 Add PostgreSQL migration and contract tests for old-row compatibility, revision foreign keys, atomic stale writes and idempotency

## 4. Quality evaluation

- [x] 4.1 Add a versioned Secret-free evaluation schema and representative candidate, relation, recall and safety cases
- [x] 4.2 Add deterministic metric computation and offline runner with thresholds and non-zero failure behavior
- [x] 4.3 Add explicit live-model extraction mode reusing existing model settings without database writes, plus tests that default mode cannot call a provider

## 5. Documentation and acceptance

- [x] 5.1 Update design, configuration, usage, testing, deploy, logging and OpenSpec navigation with provenance, stale and evaluation behavior
- [x] 5.2 Reconcile completed-vs-delivery OpenSpec state without marking external deployment or现场演示 tasks complete
- [x] 5.3 Run format, static checks, full tests, package builds, migration checks, document conflict search and strict OpenSpec validation; record evidence

## Acceptance evidence (2026-08-01)

- Ruff format/static checks and `git diff --check`: passed.
- Pytest: `151 passed, 9 skipped`; all skips require an explicit disposable PostgreSQL test database.
- Offline evaluation: 17 cases, candidate/relation precision and recall, Recall@K and safety pass rate all `1.0`.
- Current PostgreSQL: final `0007` checksum synchronized, migrate idempotent, health passed; destructive tests were not run against the shared RDS.
- Packaging: Server and lightweight Agent sdist/wheel builds passed; only the Server wheel contains `0007`, neither wheel contains `evals`.
- All seven active OpenSpec changes pass strict validation.
