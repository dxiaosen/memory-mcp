## 1. Domain and Profile Contracts

- [x] 1.1 Add revision confidence, verification, sensitivity, validity and Evidence source metadata domain types with invariant tests
- [x] 1.2 Add per-memory-type metadata policies to MemoryProfile, update general-work and test Profiles, and enforce registry validation
- [x] 1.3 Derive trusted metadata during capture and preserve user-confirmed state through pending resolution

## 2. Persistence and Lifecycle

- [x] 2.1 Add checksum-preserving PostgreSQL migrations `0004_memory_metadata.sql` and `0005_metadata_rollback_compat.sql` with compatibility defaults and constraints
- [x] 2.2 Update in-memory and PostgreSQL mappings, writes, owner-scoped validity filtering and contract tests
- [x] 2.3 Add idempotent owner-scoped revoke application/repository behavior and lifecycle tests

## 3. MCP and Agent Contracts

- [x] 3.1 Extend completed-turn message and Evidence DTOs with optional structured source metadata while preserving old payload compatibility
- [x] 3.2 Return revision metadata from list/detail/history/recall and render verification/validity boundaries within the token budget
- [x] 3.3 Expose scope-guarded `revoke_memory`, stable errors and cross-owner negative tests

## 4. Documentation and Verification

- [x] 4.1 Update total design, usage, testing and migration documentation with metadata meanings, security boundaries and rollback
- [x] 4.2 Run formatting, static checks, full tests, PostgreSQL migration/health, package builds and OpenSpec strict validation
