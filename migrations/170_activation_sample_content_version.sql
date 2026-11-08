-- Version Activation sample copy independently from promotion/audit metadata.
-- A content edit advances the version; adoption timestamps and promotion links do not.
ALTER TABLE sample_contents ADD COLUMN content_version INTEGER NOT NULL DEFAULT 1
    CHECK (content_version > 0);

CREATE TRIGGER IF NOT EXISTS trg_activation_sample_content_version
AFTER UPDATE OF title, body, hashtags_json ON sample_contents
WHEN OLD.title IS NOT NEW.title
  OR OLD.body IS NOT NEW.body
  OR OLD.hashtags_json IS NOT NEW.hashtags_json
BEGIN
    UPDATE sample_contents
       SET content_version = OLD.content_version + 1
     WHERE id = NEW.id;
END;
