-- 保证已经执行 0004 后，旧版 Server 短期回滚时仍可写入。
-- 新版应用始终显式传入可信 observed_at；默认值只服务兼容路径。
ALTER TABLE memory_revisions
    ALTER COLUMN valid_from SET DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE memory_review_items
    ALTER COLUMN valid_from SET DEFAULT CURRENT_TIMESTAMP;
