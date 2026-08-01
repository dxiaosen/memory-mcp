## 1. Domain and Profile contract

- [x] 1.1 Add relation status/model/summary domain types with time, self-loop and state invariants
- [x] 1.2 Replace `allowed_relations` and `relation_rules` with validated `MemoryRelationPolicy` mappings, and tighten recall-priority validation
- [x] 1.3 Define the bounded investment-research relation vocabulary while keeping `general-work` relation-free

## 2. Persistence and application

- [x] 2.1 Add checksum-tracked `0006_memory_relations.sql` with Profile catalog, same-owner/Profile endpoint constraints, lifecycle checks and indexes
- [x] 2.2 Extend Repository ports plus in-memory and PostgreSQL adapters with idempotent link, owner-scoped revoke and active/history relation queries
- [x] 2.3 Add MemoryService relation validation, stable exceptions and operational logging without content or owner leakage

## 3. Transport and recall

- [x] 3.1 Add relation DTOs to memory detail/history and recall responses without breaking existing request payloads
- [x] 3.2 Register strict `link_memories` and `revoke_memory_relation` MCP tools with write/review scopes and stable error mapping
- [x] 3.3 Add bounded one-hop relation ranking/rendering after owner/Profile/effective filtering and preserve item/token budgets

## 4. Verification and documentation

- [x] 4.1 Add Profile/domain/application tests for valid directions, invalid policies, owner/Profile/self/inactive boundaries, idempotency and replacement stability
- [x] 4.2 Add migration, PostgreSQL contract, MCP scope/strict-argument/cross-owner and relation-aware recall tests
- [x] 4.3 Update README, total design, configuration, usage, testing, deployment and document navigation with relation semantics, migration and non-goals
- [x] 4.4 Run format/static/full tests, package builds, migration compatibility checks, document conflict search and strict OpenSpec validation
