## ADDED Requirements

### Requirement: Review confirmation can promote to team memory
The `confirm_pending_memory` tool SHALL accept an optional `promote_to_team` parameter. When the parameter is provided, the Server SHALL validate that the authenticated principal's `team_ids` contains that team, then write the confirmed memory with the team owner key instead of the personal owner key. When the parameter is absent, the memory is written to the personal owner as before.

#### Scenario: Promote to team on confirmation
- **WHEN** a principal with `team_ids: ["research-dept"]` confirms a pending review with `promote_to_team: "research-dept"`
- **THEN** the resolved memory's `owner_id` is the team owner key `tenant-001:team:research-dept`

#### Scenario: Promote to unauthorized team is rejected
- **WHEN** a principal without `research-dept` in `team_ids` confirms with `promote_to_team: "research-dept"`
- **THEN** the Server rejects the request and does not write the memory

#### Scenario: Default confirmation writes personal owner
- **WHEN** a principal confirms a pending review without `promote_to_team`
- **THEN** the resolved memory's `owner_id` is the personal owner key

### Requirement: Capture hot path is unchanged
The `capture_completed_turn` tool SHALL continue writing candidates to the personal owner and creating pending reviews as before. Team promotion SHALL only happen through the explicit review confirmation path, not through automatic commonality detection during capture.

#### Scenario: Capture writes personal owner
- **WHEN** a principal captures a turn
- **THEN** all resulting candidates and reviews are owned by the personal owner key, regardless of team membership
