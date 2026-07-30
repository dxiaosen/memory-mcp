## ADDED Requirements

### Requirement: Authenticate every remote memory operation
Every remote capture, recall, list, detail, pending-review, confirmation, and rejection
operation MUST require authenticated MCP request context. The service MUST reject the
request before entering Memory Core when identity is absent or invalid.

#### Scenario: MCP request has no valid token
- **WHEN** a client calls a memory tool without valid authentication
- **THEN** the server returns `unauthenticated`
- **AND** performs no memory read or write

### Requirement: Separate memory owner from calling Agent
The system MUST derive memory owner scope from trusted tenant and subject identity while
recording the calling client or Agent separately. Different Agents acting for the same
owner MUST share eligible memory; different owners using the same Agent MUST remain
isolated.

#### Scenario: Two Agents act for one user
- **WHEN** user A connects through Agent A and Agent B with distinct client identifiers
- **THEN** both calls map to the same owner scope
- **AND** the audit metadata preserves the distinct client identifiers

#### Scenario: Two users share one Agent application
- **WHEN** user A and user B both use Agent B
- **THEN** their memory scopes remain distinct
- **AND** no private memory crosses between them

### Requirement: Never trust owner fields from tools or models
MCP tool schemas MUST NOT expose owner, tenant, or impersonation arguments. Model output
containing identity values MUST be ignored. Internal `PrincipalContext` MUST be created
only by the server authentication adapter.

#### Scenario: Client attempts owner impersonation
- **WHEN** a tool request includes an undeclared `owner_id` field
- **THEN** schema validation rejects the request
- **AND** the requested owner is never queried

### Requirement: Enforce operation scopes
The server SHALL enforce `memory:read`, `memory:write`, and `memory:review` permissions
from trusted request context before invoking corresponding application operations.

#### Scenario: Read-only client attempts capture
- **WHEN** a token with `memory:read` but without `memory:write` calls capture
- **THEN** the server returns `permission_denied`
- **AND** no capture record is created

#### Scenario: Write client attempts review confirmation
- **WHEN** a token lacks `memory:review` and attempts to confirm a pending item
- **THEN** the server returns `permission_denied`
- **AND** the review remains pending

### Requirement: Enforce isolation at application and storage boundaries
Every user-data Repository query and mutation MUST remain scoped by
`PrincipalContext.owner_id` even after transport-level authentication. Cross-user
identifier access MUST reveal neither content nor ownership.

#### Scenario: Authenticated user guesses another memory identifier
- **WHEN** user B supplies a memory identifier owned by user A
- **THEN** the result is indistinguishable from an unavailable identifier
- **AND** no content or owner detail is exposed

#### Scenario: Authenticated user guesses another review identifier
- **WHEN** user B supplies a pending review identifier owned by user A
- **THEN** the result is indistinguishable from an unavailable review
- **AND** no state changes

### Requirement: Keep pending content under user control
Pending content MUST remain separate from active memory. A user with review permission
SHALL be able to list, inspect, confirm, or reject pending items in their own scope.

#### Scenario: User confirms pending memory
- **WHEN** the current user confirms an owned pending item
- **THEN** it becomes an active memory exactly once
- **AND** the decision is visible in the structured result

#### Scenario: User rejects pending memory
- **WHEN** the current user rejects an owned pending item
- **THEN** it remains unavailable to future recall
- **AND** repeated rejection does not create another state transition

### Requirement: Protect sensitive content across MCP boundaries
Sensitive blocking MUST prevent prohibited raw text from appearing in long-term storage,
MCP tool results, logs, and review interfaces. The system MUST expose only a non-content
category and count for blocked outcomes.

#### Scenario: Sensitive content is blocked
- **WHEN** a capture request contains configured prohibited content
- **THEN** the response reports a blocked outcome without raw text
- **AND** later list and recall operations cannot retrieve that text

### Requirement: Expose prototype identity limitations
The project MUST clearly state that environment-configured demo tokens and virtual users
demonstrate logical isolation but do not constitute production authentication,
authorization, OAuth deployment, or compliance certification.

#### Scenario: Reviewer inspects the demo
- **WHEN** the service or documentation presents the virtual-user token setup
- **THEN** it labels the setup as prototype identity
- **AND** it does not claim production-grade security

### Requirement: Produce non-content operational audit metadata
The server SHALL log stable request, event, client, owner-reference, tool, status, count,
and duration fields for significant MCP operations. It MUST NOT log bearer tokens,
message content, memory content, source expressions, or prohibited raw text.

#### Scenario: Cross-Agent capture succeeds
- **WHEN** Agent A captures memory for an authenticated owner
- **THEN** the log records opaque owner and client references, tool, status, counts, and duration
- **AND** contains no user-message or memory content

### Requirement: Remain independent of an Agent platform
The remote MCP service MUST expose its complete product contract through authenticated
Streamable HTTP and MUST NOT require a specific Agent platform, cloud orchestration
product, or vendor SDK. Platform-specific adapters MAY configure the standard endpoint
but MUST NOT change ownership, lifecycle, or tool semantics.

#### Scenario: Two different MCP hosts connect directly
- **WHEN** two compatible Agent hosts connect to the same HTTPS MCP endpoint with their own credentials
- **THEN** both discover and call the same versioned memory tools
- **AND** neither requires an Alibaba Cloud Model Studio runtime or another intermediary

### Requirement: Do not confuse service credentials with end-user identity
A shared platform or application credential MUST NOT be used to represent multiple
end users as one owner. The authentication boundary MUST map every accepted credential
to exactly one trusted tenant and subject identity, while recording the calling client
separately.

#### Scenario: One credential is configured for a shared Agent application
- **WHEN** the application cannot provide a distinct trusted subject for each end user
- **THEN** the service treats the integration as a single-owner prototype
- **AND** documentation does not claim end-user isolation for that integration

### Requirement: Protect the public deployment boundary
The deployed MCP endpoint MUST use HTTPS, require authentication on every memory
operation, and keep PostgreSQL unreachable from the public Internet. Secrets MUST be
loaded from runtime configuration and MUST NOT be stored in source code, service unit
files, URLs, or logs.

#### Scenario: Agent calls the public service
- **WHEN** an Agent sends an authenticated request to the public MCP URL
- **THEN** TLS terminates before the request reaches the MCP application
- **AND** the application reaches PostgreSQL only through the deployment's private network
