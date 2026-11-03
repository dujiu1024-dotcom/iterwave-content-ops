-- 138: strategy steps require explicit business/effect gates; quantity is only one gate.
ALTER TABLE sub_plan_stages ADD COLUMN business_completion_required INTEGER NOT NULL DEFAULT 0
    CHECK(business_completion_required IN (0,1));
ALTER TABLE sub_plan_stages ADD COLUMN business_completion_status TEXT NOT NULL DEFAULT 'not_required'
    CHECK(business_completion_status IN ('not_required','pending','passed','failed','needs_review'));
ALTER TABLE sub_plan_stages ADD COLUMN effect_required INTEGER NOT NULL DEFAULT 0
    CHECK(effect_required IN (0,1));
ALTER TABLE sub_plan_stages ADD COLUMN effect_status TEXT NOT NULL DEFAULT 'not_required'
    CHECK(effect_status IN ('not_required','pending','passed','failed','needs_review'));

-- Existing textual completion criteria are real business gates, not merely descriptive notes.
UPDATE sub_plan_stages
SET business_completion_required=1,
    business_completion_status='pending'
WHERE length(trim(COALESCE(completion_criteria, completion_condition, ''))) > 0;

CREATE TABLE IF NOT EXISTS strategy_step_condition_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id INTEGER NOT NULL REFERENCES sub_plan_stages(id) ON DELETE RESTRICT,
    condition_type TEXT NOT NULL CHECK(condition_type IN ('business','effect')),
    decision TEXT NOT NULL CHECK(decision IN ('passed','failed','needs_review')),
    reason TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_step_condition_reviews_stage
    ON strategy_step_condition_reviews(stage_id, condition_type, created_at DESC, id DESC);

-- A declared effect-evaluation method is a required gate unless the plan explicitly opts out.
UPDATE sub_plan_stages
SET effect_required=1,
    effect_status=COALESCE((
        SELECT CASE assessment
            WHEN 'effective' THEN 'passed'
            WHEN 'ineffective' THEN 'failed'
            WHEN 'mixed' THEN 'needs_review'
            WHEN 'insufficient_data' THEN 'needs_review'
            ELSE 'pending'
        END
        FROM strategy_effect_reviews r
        WHERE r.stage_id=sub_plan_stages.id
        ORDER BY r.created_at DESC,r.id DESC LIMIT 1
    ), 'pending')
WHERE length(trim(COALESCE(effect_evaluation,''))) > 0;

-- A completed legacy stage cannot remain completed when migration introduces
-- a newly required business/effect gate that has not yet been reviewed.
UPDATE sub_plan_stages
SET status='in_progress',
    updated_at=CURRENT_TIMESTAMP
WHERE status='completed'
  AND (
      (business_completion_required=1 AND business_completion_status!='passed')
      OR (effect_required=1 AND effect_status!='passed')
  );

