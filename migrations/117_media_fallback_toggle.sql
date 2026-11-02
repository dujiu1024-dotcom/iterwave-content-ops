-- 图片创作兜底模型开关：0 = 只用主模型，失败直接报错；1 = 允许切换到兜底模型。
-- 已有租户默认保持开启，行为与升级前一致。
ALTER TABLE media_generation_settings ADD COLUMN fallback_enabled INTEGER NOT NULL DEFAULT 1;
