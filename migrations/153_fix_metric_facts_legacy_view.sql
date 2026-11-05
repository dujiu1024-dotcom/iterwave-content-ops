-- 153: replace the broken metric_facts_effective view without mutating migration 151.
DROP VIEW IF EXISTS metric_facts_latest;
DROP VIEW IF EXISTS metric_facts_effective;
CREATE VIEW metric_facts_effective AS
SELECT f.* FROM metric_facts f
WHERE NOT EXISTS (
 SELECT 1 FROM metric_facts correction
 WHERE f.source_record_type='metric_snapshot'
 AND correction.source_record_type='metric_snapshot'
 AND correction.tenant_id=f.tenant_id AND correction.content_id=f.content_id
 AND correction.correction_of=f.source_record_id AND correction.metric_key=f.metric_key
)
UNION ALL
SELECT 'legacy-metrics:' || CAST(m.id AS TEXT) || ':' || metric.metric_key,
 'legacy',m.draft_id,m.content_id,m.platform,metric.metric_key,metric.metric_value,
 names.unit,'platform_readback','metrics',CAST(m.id AS TEXT),m.fetched_at,m.fetched_at,
 NULL,NULL,'UTC',CASE WHEN m.fetch_status='success' THEN 'succeeded' ELSE 'partial' END,
 NULL,CURRENT_TIMESTAMP
FROM metrics m
JOIN (
 SELECT 'views' AS metric_key,'count' AS unit UNION ALL SELECT 'likes','count'
 UNION ALL SELECT 'collects','count' UNION ALL SELECT 'comments','count'
 UNION ALL SELECT 'shares','count' UNION ALL SELECT 'new_followers','count'
 UNION ALL SELECT 'like_rate','ratio' UNION ALL SELECT 'collect_rate','ratio'
 UNION ALL SELECT 'interaction_rate','ratio' UNION ALL SELECT 'follower_efficiency','ratio'
) names
JOIN (
 SELECT id,'views' AS metric_key,views AS metric_value FROM metrics UNION ALL
 SELECT id,'likes',likes FROM metrics UNION ALL SELECT id,'collects',collects FROM metrics UNION ALL
 SELECT id,'comments',comments FROM metrics UNION ALL SELECT id,'shares',shares FROM metrics UNION ALL
 SELECT id,'new_followers',new_followers FROM metrics UNION ALL SELECT id,'like_rate',like_rate FROM metrics UNION ALL
 SELECT id,'collect_rate',collect_rate FROM metrics UNION ALL SELECT id,'interaction_rate',interaction_rate FROM metrics UNION ALL
 SELECT id,'follower_efficiency',follower_efficiency FROM metrics
) metric ON metric.id=m.id AND metric.metric_key=names.metric_key
WHERE m.fetch_status IN ('success','partial') AND m.fetched_at IS NOT NULL
 AND metric.metric_value IS NOT NULL
 AND NOT EXISTS (
  SELECT 1 FROM metric_facts f WHERE f.source_record_type='metrics'
  AND f.source_record_id=CAST(m.id AS TEXT) AND f.metric_key=metric.metric_key
);
CREATE VIEW metric_facts_latest AS
SELECT ranked.* FROM (
 SELECT f.*,ROW_NUMBER() OVER (
  PARTITION BY f.tenant_id,f.content_id,f.metric_key
  ORDER BY f.measured_at DESC,f.recorded_at DESC,f.fact_id DESC
 ) AS observation_rank FROM metric_facts_effective f
) ranked WHERE ranked.observation_rank=1;
