-- 删除 last_verified_at 死字段：该字段从未被写入（全库赋值只有 =None），
-- 后续实现事实核验流程时再前向加回。
ALTER TABLE memory_revisions DROP COLUMN IF EXISTS last_verified_at;
ALTER TABLE memory_review_items DROP COLUMN IF EXISTS last_verified_at;
