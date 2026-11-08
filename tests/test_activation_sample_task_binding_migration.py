from __future__ import annotations


def test_activation_sample_task_binding_migration_adds_nullable_unique_task_id(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sample_contents)")}
    assert "promoted_task_id" in columns
    assert "promoted_round_id" in columns
    conn.execute(
        "INSERT INTO sample_runs (id,tenant_id,platform,account_description,status,idempotency_key,created_at) "
        "VALUES ('task-bind-run','default','xhs','分享产品经理实践方法','ready','task-bind-key',CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO sample_contents "
        "(id,sample_run_id,tenant_id,platform,angle,title,body,hashtags_json,quality_status,created_at,updated_at) "
        "VALUES ('task-bind-a','task-bind-run','default','xhs','经验','标题','正文','[]','pass',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
    )
    conn.execute("UPDATE sample_contents SET promoted_task_id='queue-task-1' WHERE id='task-bind-a'")
    conn.commit()
    assert conn.execute("SELECT promoted_task_id FROM sample_contents WHERE id='task-bind-a'").fetchone()[0] == "queue-task-1"
