## 1. Production boundary cleanup

- [x] 1.1 Remove `FixedCandidateBackend` from Server source and replace its test consumers with test-support extractors
- [x] 1.2 Remove legacy Agent URL/Token aliases and update the canonical settings contract

## 2. Regression suite consolidation

- [x] 2.1 Delete evaluation, extraction, settings and package tests with stronger existing coverage
- [x] 2.2 Consolidate relation rejection variants while retaining all three safety inputs
- [x] 2.3 Run focused tests and confirm protected owner, security, transaction and transport suites remain present

## 3. Documentation cleanup

- [x] 3.1 Simplify documentation navigation and remove the redundant evaluation-results README
- [x] 3.2 Rewrite testing guidance around layers, commands, safe database use and suite ownership
- [x] 3.3 Remove duplicated test/Profile material from configuration and duplicated test/deploy/design material from usage
- [x] 3.4 Remove old Agent connection aliases and stale fixed-backend references from all reader docs

## 4. Acceptance

- [x] 4.1 Run Ruff, full pytest, offline benchmark and `git diff --check`
- [x] 4.2 Build both distributions and verify production artifacts exclude test/evaluation-only implementations
- [x] 4.3 Strictly validate OpenSpec and record final file/test/document reductions
