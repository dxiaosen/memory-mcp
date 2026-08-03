## ADDED Requirements

### Requirement: Token declares team membership
The static Token configuration SHALL accept an optional `team_ids` field declaring the teams the authenticated subject belongs to. The Server SHALL derive a team owner key for each declared team using the format `tenant_id:team:team_id`. The `PrincipalContext` SHALL carry both the personal owner key and the team owner keys, and expose a `visible_owner_ids` set containing all of them.

#### Scenario: Token with team membership
- **WHEN** a Token is configured with `team_ids: ["research-dept"]` and `tenant_id: "tenant-001"`, `subject_id: "subject-001"`
- **THEN** the PrincipalContext carries personal owner `tenant-001:subject-001` and team owner `tenant-001:team:research-dept`

#### Scenario: Token without team membership
- **WHEN** a Token is configured without `team_ids`
- **THEN** the PrincipalContext carries only the personal owner and `visible_owner_ids` equals `(personal_owner,)`

#### Scenario: Team owner key format is unambiguous
- **WHEN** a team owner key is derived
- **THEN** it uses the `team:` infix so it cannot collide with a personal owner key derived from `subject_id`

### Requirement: Recall merges personal and team memory
The recall candidate query SHALL match memories whose `owner_id` is in the requesting principal's `visible_owner_ids` set, not a single owner. The relation and evidence queries SHALL use the same owner set. Personal and team memories SHALL be ranked together by the same relevance scoring without source-based weighting.

#### Scenario: Team member recalls team memory
- **WHEN** a team member issues a recall query and a team memory matches
- **THEN** the team memory appears in the recall result alongside personal memories

#### Scenario: Non-member cannot recall team memory
- **WHEN** a principal who is not a member of team T issues a recall query
- **THEN** no memory owned by team T's owner key appears in the result

#### Scenario: Personal and team memories ranked together
- **WHEN** a recall query matches both a personal memory and a team memory
- **THEN** both are ranked by the same relevance score without source-based bonus or penalty
