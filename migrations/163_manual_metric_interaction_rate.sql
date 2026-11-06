-- 163: derive the canonical interaction-rate fact for manual metric snapshots.
-- Keep the existing platform-specific formula: shares are excluded; Douyin/Kuaishou
-- do not expose collects and therefore use likes + comments, while other platforms
-- require collects to be present. Missing inputs remain unknown (NULL), never zero.

CREATE TRIGGER IF NOT EXISTS trg_manual_metric_interaction_rate_to_facts_insert
AFTER INSERT ON metric_snapshots
WHEN NEW.source='manual'
 AND NEW.views IS NOT NULL AND NEW.views > 0
 AND NEW.likes IS NOT NULL AND NEW.comments IS NOT NULL
 AND (NEW.platform IN ('douyin','kuaishou') OR NEW.collects IS NOT NULL)
BEGIN
    INSERT INTO metric_facts(
        fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
        source_record_type,source_record_id,measured_at,recorded_at,timezone_name,
        collection_status,correction_of,created_at
    )
    VALUES(
        lower(hex(randomblob(16))),NEW.tenant_id,NEW.draft_id,NEW.content_id,NEW.platform,
        'interaction_rate',
        CASE
            WHEN NEW.platform IN ('douyin','kuaishou')
                THEN (NEW.likes + NEW.comments) * 1.0 / NEW.views
            ELSE (NEW.likes + NEW.collects + NEW.comments) * 1.0 / NEW.views
        END,
        'ratio','manual','metric_snapshot',NEW.id,NEW.measured_at,NEW.recorded_at,
        'UTC','succeeded',NEW.correction_of,COALESCE(NEW.created_at,CURRENT_TIMESTAMP)
    )
    ON CONFLICT(source_record_type,source_record_id,metric_key) DO UPDATE SET
        value=excluded.value,source=excluded.source,measured_at=excluded.measured_at,
        recorded_at=excluded.recorded_at,correction_of=excluded.correction_of,
        collection_status=excluded.collection_status;
END;

-- Backfill historical manual snapshots so all consumers see the same canonical facts.
INSERT OR IGNORE INTO metric_facts(
    fact_id,tenant_id,draft_id,content_id,platform,metric_key,value,unit,source,
    source_record_type,source_record_id,measured_at,recorded_at,timezone_name,
    collection_status,correction_of,created_at
)
SELECT lower(hex(randomblob(16))),ms.tenant_id,ms.draft_id,ms.content_id,ms.platform,
       'interaction_rate',
       CASE
           WHEN ms.platform IN ('douyin','kuaishou')
               THEN (ms.likes + ms.comments) * 1.0 / ms.views
           ELSE (ms.likes + ms.collects + ms.comments) * 1.0 / ms.views
       END,
       'ratio','manual','metric_snapshot',ms.id,ms.measured_at,ms.recorded_at,
       'UTC','succeeded',ms.correction_of,COALESCE(ms.created_at,CURRENT_TIMESTAMP)
FROM metric_snapshots ms
WHERE ms.source='manual'
  AND ms.views IS NOT NULL AND ms.views > 0
  AND ms.likes IS NOT NULL AND ms.comments IS NOT NULL
  AND (ms.platform IN ('douyin','kuaishou') OR ms.collects IS NOT NULL);
