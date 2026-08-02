## 1. Recall contract and hydration

- [x] 1.1 Add a source-free recall candidate domain contract and a batch Evidence hydration repository operation.
- [x] 1.2 Implement PostgreSQL candidate mapping without Evidence queries and one owner-scoped latest-three Evidence query.
- [x] 1.3 Implement equivalent InMemory behavior and update RecallService to hydrate only final selected items.
- [x] 1.4 Add focused query-count, Evidence-limit, owner-isolation, relation and token-budget tests without creating new test files.

## 2. Capture delivery reliability

- [x] 2.1 Make capture commit return the authoritative result and replay an identical concurrent terminal commit.
- [x] 2.2 Preserve stable idempotency conflicts for different payloads and add two-Service overlap coverage.
- [x] 2.3 Extend the Agent state atomically with final output, fixed observed time and Profile while preserving old prompt-only files.
- [x] 2.4 Handle completed, permanent failed, reprocess-required and client-warning outcomes without deleting retryable payloads.
- [x] 2.5 Retry at most one older pending capture during a later Stop and add restart/TTL/content-safe tests.

## 3. Maintenance health

- [x] 3.1 Add an in-process content-free maintenance health snapshot with disabled, starting, ok and degraded states.
- [x] 3.2 Wire runner success/failure observations into `/health` without making a maintenance-only failure return database-unhealthy.
- [x] 3.3 Add lifecycle/health/logging tests for success recovery, consecutive failures and disabled maintenance.

## 4. Evaluation and PostgreSQL verification

- [x] 4.1 Expand the investment recall corpus with empty, paraphrase, hard-negative and large-window cases and evaluate the result.
- [x] 4.2 Extend the existing disposable PostgreSQL contract with hybrid recall, batch Evidence and maintenance idempotency execution.
- [x] 4.3 Record whether the expanded benchmark justifies model-assisted retrieval; do not add a model hot-path call without evidence.

## 5. Documentation and project closeout

- [x] 5.1 Update design, configuration, Agent, testing, evaluation, logging and deployment docs with exact delivery and health semantics.
- [x] 5.2 Reconcile stale OpenSpec phase/deferred wording and document completed-change archive order without hiding unfinished real-environment acceptance.
- [x] 5.3 Run format, lint, full tests, evaluation, wheel builds, PostgreSQL health/available contracts and strict OpenSpec validation.
- [x] 5.4 Perform a final structure/configuration/logging review and report remaining production-only boundaries.
