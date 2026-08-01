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
The system MUST derive memory owner scope deterministically from validated tenant and
subject identity while recording the authenticated client separately. Static token
configuration MUST NOT duplicate the derived owner or provide an unverified Agent
identifier. Different clients acting for the same owner MUST share eligible memory;
different owners using the same client application MUST remain isolated.

#### Scenario: Two Agents act for one user
- **WHEN** user A connects through Agent A and Agent B with distinct client identifiers
- **THEN** both calls map to the same owner scope
- **AND** the audit metadata preserves the distinct client identifiers

#### Scenario: Static token establishes a principal
- **WHEN** a configured static token is accepted with tenant, subject, and scopes
- **THEN** owner scope is derived as `tenant_id:subject_id`
- **AND** the audit client identifier is an opaque one-way reference derived from the credential
- **AND** neither owner, client, nor Agent identifier is duplicated in the token mapping

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

### Requirement: Expose static identity limitations
The project MUST use neutral runtime names for environment-configured static tokens and
principal mappings. It MUST clearly state that this static authentication boundary
demonstrates logical isolation but does not constitute production authentication,
authorization, OAuth/OIDC deployment, or compliance certification.

#### Scenario: Reviewer inspects static authentication
- **WHEN** the service or documentation presents the configured token-to-principal mapping
- **THEN** runtime fields avoid `demo` and `test` naming
- **AND** documentation labels the implementation as replaceable static authentication
- **AND** it does not claim production-grade security

### Requirement: Separate service, Agent, and verification configuration
The Memory MCP service and each Agent Host MUST be configurable as independent
deployment units. The service configuration template MUST contain only settings consumed
by the service process. An Agent Host MUST require only `MEMORY_MCP_URL` and
`MEMORY_MCP_TOKEN` for its own MCP endpoint and credential under the default flow.
Timeout, policy, and retry settings MUST have built-in defaults.

Multi-identity acceptance settings, deterministic candidate fixtures, destructive-test
database URLs, and other verification-only values MUST NOT appear in the production
service template. Real-model configuration MUST use the service-owned
`MEMORY_MCP_MODEL_*` namespace. Deterministic extraction MUST be supplied only by
test-owned dependency injection, not a runtime setting. The static token-to-principal JSON mapping MUST use the neutral
`MEMORY_MCP_AUTH_TOKENS` setting.

#### Scenario: Operator prepares a service deployment
- **WHEN** the operator copies the production service environment template
- **THEN** it contains no Agent A/B identity variables or verification candidate payloads
- **AND** it contains no runtime test-backend selector
- **AND** example principal values contain only neutral tenant, subject, and scopes

#### Scenario: Agent Host prepares its integration
- **WHEN** one Agent process configures the Hook client
- **THEN** it provides only `MEMORY_MCP_URL` and `MEMORY_MCP_TOKEN` for that process
- **AND** it does not need the service DSN, model credential, or another Agent's token

### Requirement: Produce configurable operational logs
The server SHALL always log stable request, event, client, owner-reference, tool, status,
count, and duration fields for significant MCP operations. Content logging MUST be
controlled by an explicit setting independent from the ordinary log level and MUST be
disabled by default.

When content logging is disabled, the server MUST NOT log message content, memory
content, source expressions, recall queries, rendered context, or exception messages
originating from model and storage backends. When explicitly enabled in a controlled
manual-test environment, the server MAY additionally log redacted completed-turn
messages, validated non-blocked candidates, admission outcomes, persisted memory
results, recall queries, ranked memories, and rendered recall context.

Bearer tokens, PostgreSQL DSNs, model API keys, and raw text rejected by the sensitive
content guard MUST NOT be logged in either mode. Enabling content logging MUST emit a
startup warning that application content will be written to configured handlers.

#### Scenario: Cross-Agent capture succeeds
- **WHEN** Agent A captures memory for an authenticated owner with content logging disabled
- **THEN** the log records opaque owner and client references, tool, status, counts, and duration
- **AND** contains no user-message or memory content

#### Scenario: Operator enables manual content logging
- **WHEN** the service starts with explicit content logging enabled
- **THEN** capture and recall logs include application content at their major processing stages
- **AND** the log emits a warning that the mode is enabled
- **AND** configured authentication and infrastructure secrets remain absent

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
loaded from protected runtime configuration and MUST NOT be stored in source code,
service unit files, public MCP request URLs, command arguments, or logs. A PostgreSQL
DSN containing credentials MAY exist only as a protected runtime secret and MUST be
redacted as a whole outside the connection boundary.

#### Scenario: Agent calls the public service
- **WHEN** an Agent sends an authenticated request to the public MCP URL
- **THEN** TLS terminates before the request reaches the MCP application
- **AND** the application reaches PostgreSQL only through the deployment's private network
