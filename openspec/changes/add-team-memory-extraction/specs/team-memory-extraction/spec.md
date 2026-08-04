## ADDED Requirements

### Requirement: Team extraction runs periodically and discovers common memories
The Server SHALL run a periodic team extraction task that scans each team's members' personal active memories and clusters them by embedding cosine similarity. The task SHALL run at a configurable interval (default 3600 seconds) and reuse the maintenance runner infrastructure.

#### Scenario: Two members write similar content
- **WHEN** member A and member B both write memories about "投研周报用中文" with similar embeddings
- **THEN** a team pending review is created with owner_id = team_owner_id

#### Scenario: Single member content is not extracted
- **WHEN** only one member writes a memory and no other member has a similar one
- **THEN** no team review is created

### Requirement: Clustering uses embedding cosine similarity with configurable threshold
The extraction SHALL cluster memories by memory_type group, using embedding cosine similarity >= a configurable threshold (default 0.85). Clusters with fewer than a configurable minimum size (default 2) SHALL NOT produce candidates.

#### Scenario: Similarity below threshold
- **WHEN** two memories have embedding cosine similarity 0.80 (below 0.85 threshold)
- **THEN** they are not clustered together

### Requirement: Extracted candidates enter pending review for human confirmation
The extraction SHALL create `memory_review_items` with `owner_id = team_owner_id` and `status = pending`. Team members SHALL see these via `list_pending_reviews` and confirm them via `confirm_pending_memory` (which writes to team owner).

#### Scenario: Team member confirms extracted candidate
- **WHEN** a team member calls `confirm_pending_memory` on an extracted review
- **THEN** the memory is written with `owner_id = team_owner_id`

### Requirement: Extraction is idempotent
The extraction SHALL NOT create duplicate pending reviews for the same `(team_owner_id, subject, memory_type)` combination. Each extraction run SHALL be recorded in `memory_team_extraction_runs`.

#### Scenario: Repeated extraction does not duplicate
- **WHEN** the extraction runs twice and the same common content exists
- **THEN** the second run does not create a new pending review

### Requirement: Extraction failure does not affect MCP availability
Team extraction failures SHALL NOT make the MCP service unavailable. The health endpoint SHALL report a `team_extraction` sub-state.

#### Scenario: Extraction API failure
- **WHEN** the embedding API is unavailable during team extraction
- **THEN** extraction is skipped, MCP service continues, and health reports `team_extraction.state = degraded`
