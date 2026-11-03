from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import src.api.main as api_main


class _Registry:
    def __init__(self, db_file: Path):
        self.db_file = db_file

    def get(self, tenant_id):
        return {"id": tenant_id, "name": tenant_id}

    def paths(self, tenant_id, name=None):
        return SimpleNamespace(db_file=self.db_file)


def _task(tenant_id="tenant_a", round_id=7):
    return SimpleNamespace(
        task_type="round_run",
        aggregate_id=str(round_id),
        tenant_id=tenant_id,
    )


def test_resolve_task_round_numbers_reads_only_matching_tenant_database(tmp_path):
    db_file = tmp_path / "tenant.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE spiral_rounds (id INTEGER PRIMARY KEY, round_number INTEGER)")
        conn.execute("INSERT INTO spiral_rounds(id, round_number) VALUES (7, 3)")

    result = api_main._resolve_task_round_numbers(_Registry(db_file), [_task()])
    assert result == {("tenant_a", 7): 3}


def test_resolve_task_round_numbers_logs_and_skips_unreadable_tenant_database(
    tmp_path, monkeypatch, caplog,
):
    db_file = tmp_path / "tenant.db"
    db_file.touch()

    def fail_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated locked database")

    monkeypatch.setattr(api_main.sqlite3, "connect", fail_connect)
    result = api_main._resolve_task_round_numbers(_Registry(db_file), [_task()])

    assert result == {}
    assert "读取账号组轮次编号失败 tenant=tenant_a" in caplog.text
