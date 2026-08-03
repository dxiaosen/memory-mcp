-- 拆分 evidence 文档元数据到子表。
-- 主表去掉 7 个文档列，conversation_id 改为可选（文档来源可无会话上下文）。
-- 开发阶段 evidence 表为空，无数据迁移；生产环境执行前应确认无文档来源数据。

CREATE TABLE IF NOT EXISTS memory_evidence_documents (
    evidence_id UUID PRIMARY KEY,
    source_uri TEXT,
    source_title TEXT,
    source_publisher TEXT,
    published_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ,
    content_hash TEXT,
    citation_locator TEXT,
    CONSTRAINT memory_evidence_documents_uri_non_empty
        CHECK (source_uri IS NULL OR length(btrim(source_uri)) > 0),
    CONSTRAINT memory_evidence_documents_title_non_empty
        CHECK (source_title IS NULL OR length(btrim(source_title)) > 0),
    CONSTRAINT memory_evidence_documents_publisher_non_empty
        CHECK (source_publisher IS NULL OR length(btrim(source_publisher)) > 0),
    CONSTRAINT memory_evidence_documents_hash_non_empty
        CHECK (content_hash IS NULL OR length(btrim(content_hash)) > 0),
    CONSTRAINT memory_evidence_documents_locator_non_empty
        CHECK (citation_locator IS NULL OR length(btrim(citation_locator)) > 0)
);

-- 如果历史 evidence 有文档来源数据（旧库才有这些列），先迁移到子表再删列。
-- 新库 0001 已建无文档列的 evidence，此 DO 块在列不存在时跳过。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memory_evidence' AND column_name = 'source_uri'
    ) THEN
        INSERT INTO memory_evidence_documents (
            evidence_id, source_uri, source_title, source_publisher,
            published_at, retrieved_at, content_hash, citation_locator
        )
        SELECT evidence_id, source_uri, source_title, source_publisher,
               published_at, retrieved_at, content_hash, citation_locator
        FROM memory_evidence
        WHERE source_type IN ('document', 'web')
          AND NOT EXISTS (
              SELECT 1 FROM memory_evidence_documents ed
              WHERE ed.evidence_id = memory_evidence.evidence_id
          )
        ON CONFLICT (evidence_id) DO NOTHING;
    END IF;
END $$;

ALTER TABLE memory_evidence DROP CONSTRAINT IF EXISTS memory_evidence_source_uri_non_empty;
ALTER TABLE memory_evidence DROP CONSTRAINT IF EXISTS memory_evidence_source_title_non_empty;
ALTER TABLE memory_evidence DROP CONSTRAINT IF EXISTS memory_evidence_source_publisher_non_empty;
ALTER TABLE memory_evidence DROP CONSTRAINT IF EXISTS memory_evidence_content_hash_non_empty;
ALTER TABLE memory_evidence DROP CONSTRAINT IF EXISTS memory_evidence_citation_locator_non_empty;

ALTER TABLE memory_evidence DROP COLUMN IF EXISTS source_uri;
ALTER TABLE memory_evidence DROP COLUMN IF EXISTS source_title;
ALTER TABLE memory_evidence DROP COLUMN IF EXISTS source_publisher;
ALTER TABLE memory_evidence DROP COLUMN IF EXISTS published_at;
ALTER TABLE memory_evidence DROP COLUMN IF EXISTS retrieved_at;
ALTER TABLE memory_evidence DROP COLUMN IF EXISTS content_hash;
ALTER TABLE memory_evidence DROP COLUMN IF EXISTS citation_locator;

-- conversation_id 改为可选：文档来源可无会话上下文。
ALTER TABLE memory_evidence DROP CONSTRAINT IF EXISTS memory_evidence_conversation_non_empty;
ALTER TABLE memory_evidence ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE memory_evidence ADD CONSTRAINT memory_evidence_conversation_non_empty
    CHECK (conversation_id IS NULL OR length(btrim(conversation_id)) > 0);

-- 同步拆分 review_items 的文档字段到子表。
CREATE TABLE IF NOT EXISTS memory_review_item_documents (
    review_id UUID PRIMARY KEY,
    source_uri TEXT,
    source_title TEXT,
    source_publisher TEXT,
    published_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ,
    content_hash TEXT,
    citation_locator TEXT,
    CONSTRAINT memory_review_item_documents_uri_non_empty
        CHECK (source_uri IS NULL OR length(btrim(source_uri)) > 0),
    CONSTRAINT memory_review_item_documents_title_non_empty
        CHECK (source_title IS NULL OR length(btrim(source_title)) > 0),
    CONSTRAINT memory_review_item_documents_publisher_non_empty
        CHECK (source_publisher IS NULL OR length(btrim(source_publisher)) > 0),
    CONSTRAINT memory_review_item_documents_hash_non_empty
        CHECK (content_hash IS NULL OR length(btrim(content_hash)) > 0),
    CONSTRAINT memory_review_item_documents_locator_non_empty
        CHECK (citation_locator IS NULL OR length(btrim(citation_locator)) > 0)
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'memory_review_items' AND column_name = 'source_uri'
    ) THEN
        INSERT INTO memory_review_item_documents (
            review_id, source_uri, source_title, source_publisher,
            published_at, retrieved_at, content_hash, citation_locator
        )
        SELECT review_id, source_uri, source_title, source_publisher,
               published_at, retrieved_at, content_hash, citation_locator
        FROM memory_review_items
        WHERE source_type IN ('document', 'web')
          AND NOT EXISTS (
              SELECT 1 FROM memory_review_item_documents rd
              WHERE rd.review_id = memory_review_items.review_id
          )
        ON CONFLICT (review_id) DO NOTHING;
    END IF;
END $$;

ALTER TABLE memory_review_items DROP CONSTRAINT IF EXISTS memory_review_items_source_uri_non_empty;
ALTER TABLE memory_review_items DROP CONSTRAINT IF EXISTS memory_review_items_source_title_non_empty;
ALTER TABLE memory_review_items DROP CONSTRAINT IF EXISTS memory_review_items_source_publisher_non_empty;
ALTER TABLE memory_review_items DROP CONSTRAINT IF EXISTS memory_review_items_content_hash_non_empty;
ALTER TABLE memory_review_items DROP CONSTRAINT IF EXISTS memory_review_items_citation_locator_non_empty;

ALTER TABLE memory_review_items DROP COLUMN IF EXISTS source_uri;
ALTER TABLE memory_review_items DROP COLUMN IF EXISTS source_title;
ALTER TABLE memory_review_items DROP COLUMN IF EXISTS source_publisher;
ALTER TABLE memory_review_items DROP COLUMN IF EXISTS published_at;
ALTER TABLE memory_review_items DROP COLUMN IF EXISTS retrieved_at;
ALTER TABLE memory_review_items DROP COLUMN IF EXISTS content_hash;
ALTER TABLE memory_review_items DROP COLUMN IF EXISTS citation_locator;

