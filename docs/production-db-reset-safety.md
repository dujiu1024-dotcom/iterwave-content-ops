# Production SQLite reset safety

A database reset must not indiscriminately drop SQLite triggers. Migration 150 owns
the content-production-line lifecycle; in particular,
`trg_content_production_line_after_task_insert` is the creation path used by
`ProductionTaskStore.create_task()`. If it is missing, a production task can be
inserted without its CPL row and context snapshot attachment will fail.

## Required reset sequence

1. Stop the IterWave service and take a verified backup of every tenant database.
2. Reset/recreate schema using the repository migration runner.
3. Do not run blanket `DROP TRIGGER` statements. If reset logic must remove
   project-specific immutability triggers, enumerate those exact trigger names.
4. After migrations and before starting workers, run the non-destructive trigger
   integrity check for **each tenant database**:

```bash
python scripts/ensure_production_triggers.py --db /path/to/tenant/easel.db
```

The script checks and, only when absent, recreates these Migration 150 triggers:

- `trg_content_production_line_after_task_insert`
- `trg_production_task_status_to_content_line`
- `trg_content_production_line_transition_guard`
- `trg_production_tasks_require_context_before_claim`

It does not drop triggers or modify business rows. A failure is a reset blocker:
do not start production workers until the missing schema dependencies are repaired
and the check passes.

## Post-reset smoke check

Before creating a real production task, verify that all four trigger names exist
in `sqlite_master`. Then create a disposable task only in a disposable test DB
and assert that its corresponding `content_production_lines` row and immutable
context snapshot are created in the same transaction. Never use a production
business task as a trigger smoke test.
