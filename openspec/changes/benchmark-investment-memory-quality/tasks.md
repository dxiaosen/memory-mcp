## 1. Dataset contract

- [x] 1.1 Add strict case categories and category-level report structures without allowing identity or Secret fields
- [x] 1.2 Replace the minimal mixed dataset with a versioned Chinese investment benchmark covering eight memory types, six relations, semantic recall and financial safety
- [x] 1.3 Add schema and coverage tests for unique IDs, required investment dimensions and invalid fields

## 2. Runner and reporting

- [x] 2.1 Add safe run metadata identifying live versus deterministic tasks, model/prompt/schema versions, dataset hash and duration
- [x] 2.2 Add explicit JSON output with existing-parent validation and no implicit provider call in offline mode
- [x] 2.3 Add focused tests for category results, safe serialization, output failures and the explicit live-model boundary

## 3. Live benchmark

- [x] 3.1 Run the offline benchmark and classify deterministic recall/safety failures without weakening gold labels
- [x] 3.2 Explicitly load `server/.env`, run the configured live model benchmark and retain a safe JSON snapshot
- [x] 3.3 Review failed case IDs by investment category and record evidence without copying case content or Secret values

## 4. Documentation and acceptance

- [x] 4.1 Add a dedicated evaluation report and update design, testing, usage, README and OpenSpec navigation
- [x] 4.2 Run Ruff, full pytest, offline evaluation, package boundary/build checks, diff check and strict validation
- [x] 4.3 Record final test/model evidence and remaining limitations in this change

## Acceptance evidence (2026-08-01, final)

- Dataset: `investment-memory-v2-2026-08-01`, 47 cases (candidate 16, relation 13, recall 10, safety 8), all eight investment memory types and six relation types covered.
- Offline: candidate/relation are unevaluated (`null`); Recall@K `1.0`; safety `1.0`; thresholds met without provider, network or database access.
- Live model: `deepseek:deepseek-v4-flash`; candidate precision/recall `1.0/1.0`, relation `1.0/1.0`, Recall@K `1.0`, safety `1.0`, duration 43.066 seconds; all thresholds met and no failed case IDs.
- Safe snapshot: `evals/results/investment-memory-v2-deepseek-v4-flash-2026-08-01.json`; content/Secret scan passed and only IDs plus aggregate metadata are retained.
- Verification: Ruff passed; pytest `152 passed, 9 skipped`; offline runner and `git diff --check` passed; both workspace packages built successfully.
- Package boundary: neither Server nor Agent wheel/sdist contains `evals/` or benchmark results.
- OpenSpec: the benchmark and reconciled relation-quality changes pass strict validation.
- Limitation: this is a single-model, single-run research snapshot, not statistical significance, factual verification, investment advice, production load or SLA evidence.

## 5. Evaluation integrity and cleanup

- [x] 5.1 Remove gold-label baseline predictions and report candidate/relation as unevaluated in offline mode
- [x] 5.2 Remove the test-only live predictor injection and reduce evaluation tests to product-level contracts
- [x] 5.3 Consolidate benchmark result documentation around `docs/evaluation.md`

## 6. Quality improvements

- [x] 6.1 Strengthen relation extraction direction instructions and reject explicitly negated or clearly reversed relation evidence
- [x] 6.2 Add validated Profile-driven recall hints and bounded generic scoring support
- [x] 6.3 Add focused regression coverage for relation negation and semantic recall ranking

## 7. Re-evaluation and acceptance

- [x] 7.1 Run Ruff, focused/full pytest, offline evaluation, build/package boundary and diff checks
- [x] 7.2 Run the configured live model benchmark and update the safe result snapshot and evaluation report
- [x] 7.3 Strictly validate the reconciled OpenSpec change and record final evidence
