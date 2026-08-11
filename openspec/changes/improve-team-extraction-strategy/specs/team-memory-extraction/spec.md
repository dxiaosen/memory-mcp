## ADDED Requirements

### Requirement: Subject and content selection is deterministic
The extraction SHALL select the cluster's representative `subject` and `content` using a stable, deterministic ordering (frequency desc, then lexicographic asc for subject; frequency desc, then length desc, then lexicographic asc for content). Tie-breaking SHALL NOT depend on set hash order.

#### Scenario: Two members write different subjects with equal frequency
- **WHEN** member A writes subject "框架B" and member B writes subject "框架A", each once
- **THEN** the team candidate subject is "框架A" (lexicographic smallest), reproducible across runs

### Requirement: Divergent minority views are preserved in save_rationale
When a cluster contains members whose content diverges from the main content (token Jaccard similarity below 0.6), the extraction SHALL append a divergence summary to `save_rationale` quoting the minority member's content prefix (up to 40 characters) and owner identifier. The main content's source member SHALL NOT be quoted as divergent.

#### Scenario: Cluster has one main view and one minority view
- **WHEN** two members write semantically similar but differently-worded content
- **THEN** the team candidate `save_rationale` contains a "分歧视角" section quoting the minority content, and the main content is not quoted as divergent

#### Scenario: All members write identical content
- **WHEN** all cluster members write the same content
- **THEN** `save_rationale` has no divergence section and equals the base rationale

### Requirement: Topic-level merging groups semantically distant but topically related memories
The extraction SHALL perform a second-pass topic-level merge on memories declared in the Profile's `team_extraction_topic_groups` that are NOT already in a valid embedding cluster. Memories in the same topic group SHALL be merged by subject keyword Jaccard similarity >= 0.25, allowing cross-memory-type merging within the group. Topic clusters SHALL produce a team candidate with a synthesized subject (`team:topic:{group_name}:{keyword}`) and an averaged embedding, distinguished from embedding clusters to avoid idempotency conflicts.

#### Scenario: Risk and thesis with orthogonal embeddings but shared keywords
- **WHEN** member A writes a risk with embedding orthogonal to member B's thesis, but their subjects share keywords (Jaccard >= 0.25)
- **THEN** a team candidate is produced via topic-level merging with a `team:topic:` prefixed subject

#### Scenario: Profile without topic_groups uses embedding-only clustering
- **WHEN** the Profile does not declare `team_extraction_topic_groups`
- **THEN** only embedding cosine similarity clustering runs; orthogonal embeddings produce no candidate

## MODIFIED Requirements

### Requirement: Clustering uses embedding cosine similarity with configurable threshold
The extraction SHALL cluster memories by memory_type group, using embedding cosine similarity >= a configurable threshold (default 0.70). Clusters with fewer than a configurable minimum size (default 2) SHALL NOT produce candidates. The 0.70 default (not 0.85) reflects that investment-research team commonality often shares semantics but differs in wording; 0.85 is too strict and causes under-clustering.

#### Scenario: Similarity below threshold
- **WHEN** two memories have embedding cosine similarity 0.60 (below 0.70 threshold)
- **THEN** they are not clustered together (unless topic-level merging applies)

### Requirement: Extraction is idempotent
The extraction SHALL NOT create duplicate pending reviews for the same `(team_owner_id, subject, memory_type)` combination. Topic cluster subjects use the `team:topic:` prefix to avoid collision with embedding cluster subjects. Each extraction run SHALL be recorded in `memory_team_extraction_runs`.

#### Scenario: Repeated extraction does not duplicate
- **WHEN** the extraction runs twice and the same common content exists
- **THEN** the second run does not create a new pending review
