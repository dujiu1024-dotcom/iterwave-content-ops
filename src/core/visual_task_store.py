"""Persistent lifecycle and per-page checkpoints for AI image-card generation.

The store uses the same SQLite database as the application. SQLite IMMEDIATE
transactions provide cross-process serialization; leases recover after crashes.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path


class VisualTaskBusy(RuntimeError):
    """Another worker currently owns this account group's visual-generation lease."""


class VisualTaskStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def acquire(self, task_id: str, account_group: str, owner: str | None = None,
                lease_seconds: int = 180) -> str:
        owner = owner or uuid.uuid4().hex
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            expired = conn.execute(
                "SELECT task_id FROM visual_generation_account_leases WHERE expires_at<=?",
                (now,),
            ).fetchall()
            for expired_lease in expired:
                stale = conn.execute(
                    "SELECT status FROM visual_generation_tasks WHERE task_id=?",
                    (expired_lease["task_id"],),
                ).fetchone()
                if stale and stale["status"] in ("running", "retrying"):
                    conn.execute(
                        """UPDATE visual_generation_tasks SET status='interrupted',owner=NULL,
                           lease_expires_at=NULL,error='worker lease expired',updated_at=?
                           WHERE task_id=?""",
                        (now, expired_lease["task_id"]),
                    )
                    conn.execute(
                        """INSERT INTO visual_generation_task_events
                           (task_id,from_status,to_status,reason,created_at)
                           VALUES(?,?,'interrupted','worker lease expired',?)""",
                        (expired_lease["task_id"], stale["status"], now),
                    )
            conn.execute(
                "DELETE FROM visual_generation_account_leases WHERE expires_at<=?",
                (now,),
            )
            lease = conn.execute(
                "SELECT task_id, owner, expires_at FROM visual_generation_account_leases WHERE account_group=?",
                (account_group,),
            ).fetchone()
            if lease and lease["owner"] != owner:
                raise VisualTaskBusy(f"账号组 {account_group} 正在执行图片生成任务")
            previous = conn.execute(
                "SELECT status FROM visual_generation_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            next_status = "retrying" if previous else "running"
            conn.execute(
                """INSERT INTO visual_generation_tasks
                   (task_id,account_group,status,owner,lease_expires_at,started_at,heartbeat_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(task_id) DO UPDATE SET status='retrying',owner=excluded.owner,
                   lease_expires_at=excluded.lease_expires_at,started_at=excluded.started_at,
                   heartbeat_at=excluded.heartbeat_at,finished_at=NULL,error=NULL,
                   updated_at=excluded.updated_at""",
                (task_id, account_group, next_status, owner, now + lease_seconds, now, now, now, now),
            )
            conn.execute(
                """INSERT INTO visual_generation_task_events
                   (task_id,from_status,to_status,reason,created_at) VALUES(?,?,?,?,?)""",
                (task_id, previous["status"] if previous else None, next_status, "worker acquired lease", now),
            )
            conn.execute(
                """INSERT INTO visual_generation_account_leases
                   (account_group,task_id,owner,expires_at,updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(account_group) DO UPDATE SET task_id=excluded.task_id,
                   owner=excluded.owner,expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
                (account_group, task_id, owner, now + lease_seconds, now),
            )
            conn.commit()
            return owner
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def heartbeat(self, task_id: str, owner: str, lease_seconds: int = 180) -> bool:
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """UPDATE visual_generation_account_leases SET expires_at=?,updated_at=?
                   WHERE task_id=? AND owner=? AND expires_at>?""",
                (now + lease_seconds, now, task_id, owner, now),
            )
            if cur.rowcount:
                conn.execute(
                    """UPDATE visual_generation_tasks SET heartbeat_at=?,lease_expires_at=?,updated_at=?
                       WHERE task_id=? AND owner=?""",
                    (now, now + lease_seconds, now, task_id, owner),
                )
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()

    def completed_page(self, task_id: str, page_index: int) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status,output_path FROM visual_generation_pages WHERE task_id=? AND page_index=?",
                (task_id, page_index),
            ).fetchone()
            if row and row["status"] == "completed":
                if row["output_path"] and Path(row["output_path"]).is_file():
                    return row["output_path"]
                raise FileNotFoundError(
                    f"completed visual page artifact is missing: task={task_id}, page={page_index}"
                )
            if row is None:
                task = conn.execute(
                    "SELECT status FROM visual_generation_tasks WHERE task_id=?",
                    (task_id,),
                ).fetchone()
                if task and task["status"] == "completed":
                    raise FileNotFoundError(
                        f"completed visual task has no page checkpoint: task={task_id}, page={page_index}"
                    )
            if row and row["status"] == "running" and row["output_path"] and Path(row["output_path"]).is_file():
                return row["output_path"]
            return None
        finally:
            conn.close()

    def start_page(self, task_id: str, page_index: int):
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO visual_generation_pages(task_id,page_index,status,attempts,updated_at)
                   VALUES(?,?,'running',1,?)
                   ON CONFLICT(task_id,page_index) DO UPDATE SET status='running',
                   attempts=attempts+1,error=NULL,updated_at=excluded.updated_at""",
                (task_id, page_index, now),
            )
        finally:
            conn.close()

    def set_page_output(self, task_id: str, page_index: int, output_path: str):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE visual_generation_pages SET output_path=?,updated_at=? WHERE task_id=? AND page_index=?",
                (output_path, time.time(), task_id, page_index),
            )
        finally:
            conn.close()

    def complete_page(self, task_id: str, page_index: int, output_path: str):
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE visual_generation_pages SET status='completed',output_path=?,error=NULL,updated_at=?
                   WHERE task_id=? AND page_index=?""",
                (output_path, time.time(), task_id, page_index),
            )
        finally:
            conn.close()

    def fail_page(self, task_id: str, page_index: int, error: str):
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE visual_generation_pages SET status='failed',error=?,updated_at=?
                   WHERE task_id=? AND page_index=?""",
                (str(error)[:500], time.time(), task_id, page_index),
            )
        finally:
            conn.close()

    def finish(self, task_id: str, owner: str, status: str, error: str | None = None):
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT status FROM visual_generation_tasks WHERE task_id=? AND owner=?",
                (task_id, owner),
            ).fetchone()
            cursor = conn.execute(
                """UPDATE visual_generation_tasks SET status=?,finished_at=?,error=?,
                   owner=NULL,lease_expires_at=NULL,updated_at=? WHERE task_id=? AND owner=?""",
                (status, now, str(error)[:500] if error else None, now, task_id, owner),
            )
            if cursor.rowcount and previous:
                conn.execute(
                    """INSERT INTO visual_generation_task_events
                       (task_id,from_status,to_status,reason,created_at) VALUES(?,?,?,?,?)""",
                    (task_id, previous["status"], status, str(error)[:500] if error else None, now),
                )
            conn.execute(
                "DELETE FROM visual_generation_account_leases WHERE task_id=? AND owner=?",
                (task_id, owner),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
