## ADDED Requirements

### Requirement: Embedding is computed and stored at capture time
The Server SHALL compute an embedding vector for each auto-saved MemoryRevision's content at capture time and store it in a `vector(1024)` column using pgvector. If the embedding API is unavailable, the revision SHALL be stored with `embedding=NULL` and the capture SHALL NOT fail.

#### Scenario: Embedding computed on auto-save
- **WHEN** a candidate is auto-saved with content "投研周报用中文"
- **THEN** the revision's `embedding` column contains a 1024-dimension float vector

#### Scenario: Embedding API failure does not block capture
- **WHEN** the embedding API returns an error during capture
- **THEN** the memory is stored with `embedding=NULL` and capture status is `completed`

### Requirement: Recall uses three-way hybrid candidate retrieval
The Repository SHALL retrieve recall candidates from three sources: lexical (trigram, 40%), vector (embedding cosine, 30%), and recent (time-ordered, 30%). Vector candidates SHALL use pgvector's `<=>` cosine distance operator. Candidates without embeddings SHALL only participate in lexical and recent paths.

#### Scenario: Semantic match via vector path
- **WHEN** the query is "看好新能源" and a memory contains "锂电池前景广阔"
- **THEN** the memory is retrieved via the vector path (semantic match)

#### Scenario: Vector path skipped when no embeddings
- **WHEN** no revisions in the candidate set have embeddings
- **THEN** only lexical and recent paths are used, vector_count is 0

### Requirement: Query embedding is computed at recall time
The Application SHALL compute a query embedding before calling the Repository's recall candidate query. If query embedding computation fails, the vector path SHALL be skipped and only lexical and recent paths are used.

#### Scenario: Query embedding failure degrades to two paths
- **WHEN** the embedding API is unavailable during recall
- **THEN** recall returns results from lexical and recent paths only, with no error to the Agent

### Requirement: pgvector extension is required for vector recall
The Server SHALL require the `pgvector` PostgreSQL extension. If the extension is not installed, the Server SHALL fail at startup with a clear error message.

#### Scenario: pgvector not installed
- **WHEN** the PostgreSQL instance does not have the pgvector extension
- **THEN** the Server fails to start with a message indicating pgvector is required
