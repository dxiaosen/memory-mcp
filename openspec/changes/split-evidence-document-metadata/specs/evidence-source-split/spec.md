## ADDED Requirements

### Requirement: Evidence main table holds only source-universal fields
The `memory_evidence` table SHALL store only fields universal to all source types: `evidence_id`, `memory_id`, `revision_id`, `owner_id`, `source_type`, `source_role`, `source_message_id`, `source_tool_name`, `conversation_id`, `source_turn_id`, `source_expression`, `observed_at`, `created_at`. It SHALL NOT store document-specific fields (`source_uri`, `source_title`, `source_publisher`, `published_at`, `retrieved_at`, `content_hash`, `citation_locator`).

#### Scenario: Conversation source has no document columns
- **WHEN** a conversation-source evidence row is stored
- **THEN** it has no document metadata columns on the main table and no row in the document subtable

### Requirement: Document metadata lives in a separate subtable
A `memory_evidence_documents` table SHALL exist with a 1:1 relationship to `memory_evidence` via `evidence_id`. It SHALL hold `source_uri`, `source_title`, `source_publisher`, `published_at`, `retrieved_at`, `content_hash`, `citation_locator`. A row SHALL exist only when `source_type` is `document` or `web`; conversation and tool sources SHALL have no row.

#### Scenario: Document source populates subtable
- **WHEN** an evidence row has `source_type = 'document'`
- **THEN** a corresponding row exists in `memory_evidence_documents` with the document metadata

#### Scenario: Conversation source has no subtable row
- **WHEN** an evidence row has `source_type = 'conversation'`
- **THEN** no row exists in `memory_evidence_documents`

### Requirement: conversation_id is optional for document sources
The `conversation_id` column on `memory_evidence` SHALL be nullable. Conversation and tool sources SHALL still populate it; document and web sources MAY leave it null when no conversation context applies.

#### Scenario: Document source without conversation context
- **WHEN** a document-source evidence is stored without a conversation context
- **THEN** `conversation_id` is null and the insert does not fail

### Requirement: Recall renders document metadata only when present
The recall source summary SHALL expose document metadata as an optional sub-object. When the source is a conversation or tool, the document sub-object SHALL be null. When the source is a document or web, the document sub-object SHALL carry the metadata.

#### Scenario: Conversation source in recall
- **WHEN** a recalled evidence source has `source_type = 'conversation'`
- **THEN** its summary has no document metadata

#### Scenario: Document source in recall
- **WHEN** a recalled evidence source has `source_type = 'document'`
- **THEN** its summary carries a document sub-object with uri, title and citation metadata
