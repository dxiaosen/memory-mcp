## REMOVED Requirements

### Requirement: Topic-level merging groups semantically distant but topically related memories
The extraction SHALL NOT perform topic-level merging. The `team_extraction_topic_groups` Profile extension point SHALL be removed. Extraction SHALL use embedding cosine similarity clustering only, grouped by `memory_type`.

Rationale: thesis and risk are already isolated by `memory_type` grouping, so the cross-type same-topic scenario the topic merge targeted mostly does not arise. Subject keyword Jaccard at 0.25 has limited coverage under CJK single-character tokenization and introduces non-natural artifacts (synthesized `team:topic:` subjects, averaged embeddings, two-pass `assigned_ids` reconciliation).

#### Scenario: Profile without topic_groups uses embedding-only clustering
- **WHEN** the Profile does not declare `team_extraction_topic_groups`
- **THEN** only embedding cosine similarity clustering runs; orthogonal embeddings produce no candidate

## MODIFIED Requirements

### Requirement: Clustering uses embedding cosine similarity with configurable threshold
The extraction SHALL cluster memories by `memory_type` group, using embedding cosine similarity >= a configurable threshold (default 0.70). Clusters with fewer than a configurable minimum size (default 2) AND fewer than 2 distinct members SHALL NOT produce candidates. A cluster where members' `business_progress` contains both `resolved` and `invalidated` SHALL be dropped (weak direction guard: avoids merging opposing stances into a single "team consensus"; when `business_progress` is empty for all members, the guard does not trigger). The 0.70 default (not 0.85) reflects that investment-research team commonality often shares semantics but differs in wording; 0.85 is too strict and causes under-clustering.

#### Scenario: Similarity below threshold
- **WHEN** two memories have embedding cosine similarity 0.60 (below 0.70 threshold)
- **THEN** they are not clustered together

#### Scenario: Opposing business_progress drops the cluster
- **WHEN** two members write semantically similar content but one marks `business_progress=resolved` and the other `business_progress=invalidated`
- **THEN** no team candidate is produced from that cluster

#### Scenario: Empty business_progress does not trigger the guard
- **WHEN** all cluster members have `business_progress` empty (the investment-research norm)
- **THEN** the weak direction guard does not trigger and clustering proceeds normally

### Requirement: Candidate embedding uses cluster centroid
The candidate's embedding SHALL be the arithmetic mean of cluster member embeddings (cluster centroid), NOT the `cluster[0]` raw vector. This improves representativeness and ensures the idempotency embedding comparison is stable across runs (not drifting as members write new content or ordering changes).

#### Scenario: Candidate embedding is the member mean
- **WHEN** a cluster has two members with embeddings (1.0, 0.0) and (0.5, 0.5)
- **THEN** the candidate embedding is (0.75, 0.25)

### Requirement: Extraction is idempotent
The extraction SHALL NOT create duplicate reviews for the same `(team_owner_id, subject, memory_type)` combination when a review with status `pending` OR `confirmed` already exists. The PostgreSQL version SHALL additionally skip when an existing review's embedding cosine distance is < 0.05. Extending idempotency to `confirmed` prevents a consensus, once confirmed, from being re-submitted as a new pending when members keep writing similar content. Each extraction run SHALL be recorded in `memory_team_extractions`.

#### Scenario: Repeated extraction does not duplicate while pending
- **WHEN** the extraction runs twice and the same common content exists with the first candidate still pending
- **THEN** the second run does not create a new pending review

#### Scenario: Confirmed consensus is not re-submitted as pending
- **WHEN** a team candidate has been confirmed and members keep writing similar content
- **THEN** a subsequent extraction run does not create a new pending review for the same subject+type
