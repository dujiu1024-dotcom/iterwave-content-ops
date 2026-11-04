import sqlite3
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

def test_migration_adds_versioned_north_star_and_immutable_resource_snapshot():
 c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row
 c.execute("PRAGMA foreign_keys=ON")
 c.executescript("CREATE TABLE master_plan_versions(id INTEGER PRIMARY KEY,version_no INTEGER,plan_signature TEXT,plan_snapshot TEXT,status TEXT);")
 c.execute("INSERT INTO master_plan_versions(id,version_no,plan_signature,plan_snapshot,status) VALUES(99,1,'legacy','{}','archived')")
 c.executescript((ROOT/"migrations/148_account_group_north_star.sql").read_text())
 cols={r["name"] for r in c.execute("PRAGMA table_info(master_plan_versions)")}
 assert {"north_star_version_id","north_star_snapshot_json","north_star_attribution_status"} <= cols
 legacy=c.execute("SELECT north_star_version_id,north_star_snapshot_json,north_star_attribution_status FROM master_plan_versions WHERE id=99").fetchone()
 assert legacy["north_star_version_id"] is None
 assert legacy["north_star_snapshot_json"] is None
 assert legacy["north_star_attribution_status"]=="legacy_unattributed"
 cur=c.execute("INSERT INTO account_group_north_star_versions(version_no,snapshot_json,status,change_reason,created_by,created_at,effective_at) VALUES(1,'{}','effective','initial','tester','now','now')")
 c.execute("INSERT INTO master_plan_versions(id,version_no,plan_signature,plan_snapshot,status,north_star_version_id,north_star_snapshot_json) VALUES(1,1,'sig','{}','draft',?,'{}')",(cur.lastrowid,))
 c.execute("INSERT INTO master_plan_resource_baselines(master_plan_version_id,snapshot_json,captured_at,captured_by) VALUES(1,'{}','now','tester')")
 with pytest.raises(sqlite3.IntegrityError,match="immutable"):
  c.execute("UPDATE master_plan_resource_baselines SET snapshot_json='{\"x\":1}' WHERE master_plan_version_id=1")
 with pytest.raises(sqlite3.IntegrityError,match="immutable"):
  c.execute("UPDATE account_group_north_star_versions SET snapshot_json='{\"x\":1}' WHERE id=?",(cur.lastrowid,))
 with pytest.raises(sqlite3.IntegrityError,match="immutable"):
  c.execute("DELETE FROM account_group_north_star_versions WHERE id=?",(cur.lastrowid,))
 c.close()
