"""持久化全局任务队列。

队列位于 shared/system.db，与当前活动账号组无关。所有业务任务必须显式携带
tenant_id；不同账号组可并行消费，同一账号组同一时刻最多一个任务运行。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_WAL_MODE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class QueuedTask:
    queue_order: int
    task_id: str
    task_type: str
    tenant_id: str
    aggregate_id: str | None
    payload: dict[str, Any]
    status: str
    created_at: str
    available_at: str
    started_at: str | None
    finished_at: str | None
    attempts: int
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    retry_of_task_id: str | None = None
    retry_confirmed_by: str | None = None
    retry_confirmation_note: str | None = None
    resolution_action: str | None = None
    resolution_actor: str | None = None
    resolution_note: str | None = None
    resolution_at: str | None = None
    progress: dict[str, Any] | None = None


class TaskQueueError(RuntimeError):
    """任务队列操作失败。"""


class GlobalTaskQueue:
    """基于 SQLite 的多消费者队列，跨账号组共享且可在重启后恢复。

    claim_next 使用 BEGIN IMMEDIATE 原子领取；worker lease 按 owner 隔离，
    同一账号组只允许一个 running 任务，不同账号组可并行。任务租约需定期续期。
    """

    def __init__(self, system_db_file: Path | str):
        self.db_file = Path(system_db_file).resolve()
        self.db_file.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_file), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        conn = self._connect()
        try:
            # 同一进程内只设置一次；WAL 模式会持久化到数据库文件。
            with _WAL_MODE_LOCK:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError as exc:
                    # 多进程启动时，另一个进程可能正在完成首次 WAL 切换。
                    if "locked" not in str(exc).lower():
                        raise
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS global_task_queue (
                    queue_order INTEGER NOT NULL UNIQUE,
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    aggregate_id TEXT,
                    payload_json TEXT NOT NULL,
                    available_at TEXT,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','running','succeeded','failed','cancelled','interrupted')),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 1,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    retry_of_task_id TEXT,
                    retry_confirmed_by TEXT,
                    retry_confirmation_note TEXT,
                    progress_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_global_task_queue_tenant
                    ON global_task_queue(tenant_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_global_task_queue_created_at
                    ON global_task_queue(created_at);
                CREATE INDEX IF NOT EXISTS idx_global_task_queue_tenant_type_created
                    ON global_task_queue(tenant_id, task_type, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_global_task_active_aggregate
                    ON global_task_queue(tenant_id, task_type, aggregate_id)
                    WHERE aggregate_id IS NOT NULL AND status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS global_task_worker_lease (
                    singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                    owner TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS global_task_worker_leases (
                    owner TEXT PRIMARY KEY,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS global_task_actions (
                    task_id TEXT PRIMARY KEY REFERENCES global_task_queue(task_id) ON DELETE CASCADE,
                    action TEXT NOT NULL CHECK(action IN ('handled','ignored')),
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    acted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS global_task_admin_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES global_task_queue(task_id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    acted_at TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_global_task_admin_audit_task_time
                    ON global_task_admin_audit(task_id, acted_at DESC, id DESC);
                """
            )
            # 跨进程串行执行旧表迁移，避免多 Uvicorn worker 同时 ALTER 同一列。
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 兼容早期队列表：补齐显式 FIFO 序号与延迟执行时间。
                columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(global_task_queue)").fetchall()
                }
                if "queue_order" not in columns:
                    conn.execute("ALTER TABLE global_task_queue ADD COLUMN queue_order INTEGER")
                    conn.execute(
                        "UPDATE global_task_queue SET queue_order=rowid WHERE queue_order IS NULL"
                    )
                if "available_at" not in columns:
                    conn.execute("ALTER TABLE global_task_queue ADD COLUMN available_at TEXT")
                if "retry_of_task_id" not in columns:
                    conn.execute("ALTER TABLE global_task_queue ADD COLUMN retry_of_task_id TEXT")
                if "retry_confirmed_by" not in columns:
                    conn.execute("ALTER TABLE global_task_queue ADD COLUMN retry_confirmed_by TEXT")
                if "retry_confirmation_note" not in columns:
                    conn.execute("ALTER TABLE global_task_queue ADD COLUMN retry_confirmation_note TEXT")
                if "progress_json" not in columns:
                    conn.execute("ALTER TABLE global_task_queue ADD COLUMN progress_json TEXT")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_global_task_retry_parent "
                    "ON global_task_queue(retry_of_task_id) WHERE retry_of_task_id IS NOT NULL"
                )
                conn.execute(
                    "UPDATE global_task_queue SET available_at=created_at WHERE available_at IS NULL"
                )
                conn.execute("DROP INDEX IF EXISTS idx_global_task_queue_fifo")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_global_task_queue_order "
                    "ON global_task_queue(queue_order)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_global_task_queue_fifo "
                    "ON global_task_queue(status, queue_order)"
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()
        try:
            self.db_file.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> QueuedTask | None:
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
            result = json.loads(row["result_json"]) if row["result_json"] else None
        except (TypeError, ValueError) as exc:
            raise TaskQueueError("队列任务数据损坏，已拒绝执行") from exc
        if not isinstance(payload, dict) or (result is not None and not isinstance(result, dict)):
            raise TaskQueueError("队列任务数据结构无效，已拒绝执行")
        return QueuedTask(
            queue_order=int(row["queue_order"]),
            task_id=row["task_id"], task_type=row["task_type"],
            tenant_id=row["tenant_id"], aggregate_id=row["aggregate_id"],
            payload=payload, status=row["status"], created_at=row["created_at"],
            available_at=row["available_at"] or row["created_at"],
            started_at=row["started_at"], finished_at=row["finished_at"],
            attempts=int(row["attempts"]), result=result,
            error_code=row["error_code"], error_message=row["error_message"],
            retry_of_task_id=row["retry_of_task_id"],
            retry_confirmed_by=row["retry_confirmed_by"],
            retry_confirmation_note=row["retry_confirmation_note"],
            resolution_action=row["resolution_action"] if "resolution_action" in row.keys() else None,
            resolution_actor=row["resolution_actor"] if "resolution_actor" in row.keys() else None,
            resolution_note=row["resolution_note"] if "resolution_note" in row.keys() else None,
            resolution_at=row["resolution_at"] if "resolution_at" in row.keys() else None,
            progress=(
                json.loads(row["progress_json"])
                if "progress_json" in row.keys() and row["progress_json"]
                else None
            ),
        )

    def enqueue(
        self, *, task_type: str, tenant_id: str, payload: dict[str, Any],
        aggregate_id: str | None = None, max_attempts: int = 1,
        available_at: datetime | str | None = None,
    ) -> tuple[QueuedTask, bool]:
        if not task_type or len(task_type) > 80:
            raise ValueError("task_type 无效")
        if not tenant_id or len(tenant_id) > 48:
            raise ValueError("tenant_id 无效")
        if not isinstance(payload, dict):
            raise ValueError("payload 必须是对象")
        # 外部平台动作不具备通用安全重试语义；失败/中断后由操作者核实后手动重入队。
        if int(max_attempts) != 1:
            raise ValueError("当前队列不自动重试，max_attempts 必须为 1")
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if available_at is None:
            available_stamp = _stamp()
        elif isinstance(available_at, datetime):
            if available_at.tzinfo is None:
                raise ValueError("available_at 必须包含时区")
            available_stamp = _stamp(available_at.astimezone(timezone.utc))
        elif isinstance(available_at, str):
            try:
                parsed_available_at = datetime.fromisoformat(available_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("available_at 必须是带时区的 ISO 8601 时间") from exc
            if parsed_available_at.tzinfo is None:
                raise ValueError("available_at 必须包含时区")
            available_stamp = _stamp(parsed_available_at.astimezone(timezone.utc))
        else:
            raise ValueError("available_at 类型无效")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = _stamp()
            queue_order = int(conn.execute(
                "SELECT COALESCE(MAX(queue_order), 0) + 1 FROM global_task_queue"
            ).fetchone()[0])
            if aggregate_id is not None:
                existing = conn.execute(
                    "SELECT * FROM global_task_queue WHERE tenant_id=? AND task_type=? "
                    "AND aggregate_id=? AND status IN ('queued','running') "
                    "ORDER BY created_at LIMIT 1",
                    (tenant_id, task_type, str(aggregate_id)),
                ).fetchone()
                if existing:
                    conn.commit()
                    task = self._decode(existing)
                    assert task is not None
                    return task, False
            task_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO global_task_queue(queue_order,task_id,task_type,tenant_id,aggregate_id,"
                "payload_json,available_at,status,created_at,max_attempts) "
                "VALUES(?,?,?,?,?,?,?,'queued',?,?)",
                (queue_order, task_id, task_type, tenant_id,
                 str(aggregate_id) if aggregate_id is not None else None,
                 payload_json, available_stamp, now, int(max_attempts)),
            )
            row = conn.execute("SELECT * FROM global_task_queue WHERE task_id=?", (task_id,)).fetchone()
            conn.commit()
            task = self._decode(row)
            assert task is not None
            return task, True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_next(self, owner: str, *, lease_seconds: int = 60) -> QueuedTask | None:
        if not owner:
            raise ValueError("worker owner 不能为空")
        now = _now()
        expires = _stamp(now + timedelta(seconds=max(15, lease_seconds)))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            owned_task = conn.execute(
                "SELECT 1 FROM global_task_queue WHERE status='running' AND lease_owner=? LIMIT 1",
                (owner,),
            ).fetchone()
            if owned_task:
                # 不允许同一 worker 重入领取；保留其正在使用的租约供心跳续期。
                conn.commit()
                return None
            conn.execute(
                "INSERT INTO global_task_worker_leases(owner,expires_at) VALUES(?,?) "
                "ON CONFLICT(owner) DO UPDATE SET expires_at=excluded.expires_at",
                (owner, expires),
            )
            # 全局可并行消费，但同一账号组同时只运行一个任务：
            # 避免多个 worker 并发推进同一租户的轮次/调度状态，同时跳过被占用租户，
            # 让其他账号组不再被队首的长任务阻塞。
            row = conn.execute(
                "SELECT q.* FROM global_task_queue q "
                "WHERE q.status='queued' AND COALESCE(q.available_at,q.created_at)<=? "
                "AND NOT EXISTS (SELECT 1 FROM global_task_queue running "
                "                WHERE running.tenant_id=q.tenant_id AND running.status='running') "
                "AND NOT EXISTS (SELECT 1 FROM global_task_queue owned "
                "                WHERE owned.lease_owner=? AND owned.status='running') "
                "ORDER BY q.queue_order ASC LIMIT 1",
                (_stamp(now), owner),
            ).fetchone()
            if row is None:
                conn.execute(
                    "DELETE FROM global_task_worker_leases WHERE owner=?",
                    (owner,),
                )
                conn.commit()
                return None
            started = _stamp(now)
            conn.execute(
                "UPDATE global_task_queue SET status='running',started_at=?,attempts=attempts+1,"
                "lease_owner=?,lease_expires_at=? WHERE task_id=? AND status='queued'",
                (started, owner, expires, row["task_id"]),
            )
            claimed = conn.execute(
                "SELECT * FROM global_task_queue WHERE task_id=?", (row["task_id"],)
            ).fetchone()
            conn.commit()
            return self._decode(claimed)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def renew(self, task_id: str, owner: str, *, lease_seconds: int = 60) -> bool:
        expires = _stamp(_now() + timedelta(seconds=max(15, lease_seconds)))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT owner FROM global_task_worker_leases WHERE owner=? AND expires_at>?",
                (owner, _stamp()),
            ).fetchone()
            if not lease:
                conn.rollback()
                return False
            cursor = conn.execute(
                "UPDATE global_task_queue SET lease_expires_at=? "
                "WHERE task_id=? AND status='running' AND lease_owner=?",
                (expires, task_id, owner),
            )
            if cursor.rowcount:
                conn.execute(
                    "UPDATE global_task_worker_leases SET expires_at=? WHERE owner=?",
                    (expires, owner),
                )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_progress(self, task_id: str, progress: dict[str, Any]) -> bool:
        """写入任务实时进度快照；仅对排队中/运行中的任务生效。

        进度只用于界面展示，任何写入失败都不应影响任务执行，调用方负责兜底。
        """
        if not isinstance(progress, dict):
            raise ValueError("progress 必须是对象")
        progress_json = json.dumps(progress, ensure_ascii=False, sort_keys=True, default=str)
        conn = self._connect()
        try:
            statement = (
                "UPDATE global_task_queue SET progress_json=? "
                "WHERE task_id=? AND status IN ('queued','running')"
            )
            try:
                cursor = conn.execute(statement, (progress_json, task_id))
            except sqlite3.OperationalError:
                # 旧库可能尚未跑到建表迁移：就地补列后重试一次，避免进度整段丢失。
                conn.execute("ALTER TABLE global_task_queue ADD COLUMN progress_json TEXT")
                cursor = conn.execute(statement, (progress_json, task_id))
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def finish(
        self, task_id: str, owner: str, *, result: dict[str, Any] | None = None,
    ) -> bool:
        result_json = json.dumps(result or {}, ensure_ascii=False, sort_keys=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE global_task_queue SET status='succeeded',finished_at=?,result_json=?,"
                "lease_owner=NULL,lease_expires_at=NULL,error_code=NULL,error_message=NULL "
                "WHERE task_id=? AND status='running' AND lease_owner=?",
                (_stamp(), result_json, task_id, owner),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fail(
        self, task_id: str, owner: str, *, error_code: str,
        error_message: str, result: dict[str, Any] | None = None,
    ) -> bool:
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True) if result is not None else None
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE global_task_queue SET status='failed',finished_at=?,error_code=?,"
                "error_message=?,result_json=COALESCE(?,result_json),lease_owner=NULL,lease_expires_at=NULL "
                "WHERE task_id=? AND status='running' AND lease_owner=?",
                (_stamp(), error_code[:80], error_message[:500], result_json, task_id, owner),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def interrupt(
        self, task_id: str, owner: str, *,
        error_message: str = "服务正在停止，任务已中断；请核实平台侧执行结果后再恢复",
    ) -> bool:
        """由当前租约持有者主动中断任务，避免停机时留下长时间 running。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE global_task_queue SET status='interrupted',finished_at=?,"
                "error_code='worker_shutdown',error_message=?,"
                "lease_owner=NULL,lease_expires_at=NULL "
                "WHERE task_id=? AND status='running' AND lease_owner=?",
                (_stamp(), error_message[:500], task_id, owner),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recover_orphaned_running(self) -> dict[str, int]:
        """服务实例启动时，立即回收上一进程遗留的 running 任务和 worker leases。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE global_task_queue SET status='interrupted',finished_at=?,"
                "error_code='worker_restarted',"
                "error_message='服务实例已重启，原任务未自动重放；请核实平台侧执行结果后再恢复',"
                "lease_owner=NULL,lease_expires_at=NULL WHERE status='running'",
                (_stamp(),),
            )
            # 清理旧版单消费者租约和新版按 owner 分片的租约。
            conn.execute("DELETE FROM global_task_worker_lease WHERE singleton_id=1")
            conn.execute("DELETE FROM global_task_worker_leases")
            conn.commit()
            return {"interrupted": cursor.rowcount}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def release_worker(self, owner: str) -> bool:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            running = conn.execute(
                "SELECT 1 FROM global_task_queue WHERE status='running' AND lease_owner=? LIMIT 1",
                (owner,),
            ).fetchone()
            if running:
                # 不得在任务仍运行时释放全局锁；让任务租约自然过期后由恢复流程接管。
                conn.commit()
                return False
            cursor = conn.execute(
                "DELETE FROM global_task_worker_leases WHERE owner=?",
                (owner,),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def cancel(self, task_id: str, *, actor: str = "system") -> bool:
        """Cancel a queued task and append its audit event in the same transaction."""
        actor = str(actor or "").strip()
        if not actor or len(actor) > 100:
            raise ValueError("actor 无效")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            acted_at = _stamp()
            cursor = conn.execute(
                "UPDATE global_task_queue SET status='cancelled',finished_at=? "
                "WHERE task_id=? AND status='queued'",
                (acted_at, task_id),
            )
            if cursor.rowcount == 1:
                conn.execute(
                    "INSERT INTO global_task_admin_audit(task_id,action,actor,acted_at,details_json) "
                    "VALUES(?,?,?,?,?)",
                    (task_id, "cancel", actor, acted_at, "{}"),
                )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def interrupt_running_after_worker_exit(self, *, reason: str) -> dict[str, Any]:
        """Mark all running tasks interrupted after the owning worker process is confirmed dead.

        A worker process hosts multiple tenant consumers. Killing its process group can
        interrupt several tenants, so all running rows and all owner leases are reconciled
        in one transaction. Uncertain platform effects are never replayed automatically.
        """
        message = (reason or "后台 Worker 进程已退出，任务结果需人工核实")[:500]
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT task_id FROM global_task_queue WHERE status='running' ORDER BY queue_order"
            ).fetchall()
            cursor = conn.execute(
                "UPDATE global_task_queue SET status='interrupted', finished_at=?, "
                "error_code='worker_process_lost', error_message=?, "
                "lease_owner=NULL, lease_expires_at=NULL WHERE status='running'",
                (_stamp(), message),
            )
            # Clear both the legacy singleton and the current per-consumer lease table.
            conn.execute("DELETE FROM global_task_worker_lease WHERE singleton_id=1")
            conn.execute("DELETE FROM global_task_worker_leases")
            conn.commit()
            return {"interrupted": cursor.rowcount, "task_ids": [row["task_id"] for row in rows]}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recover_expired(self) -> dict[str, int]:
        """只回收租约过期的 running 任务；转为 interrupted，避免不安全地重复执行副作用。"""
        now = _stamp()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE global_task_queue SET status='interrupted',finished_at=?,"
                "error_code='worker_lost',error_message='任务执行期间服务中断，需人工确认后恢复',"
                "lease_owner=NULL,lease_expires_at=NULL "
                "WHERE status='running' AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
                (now, now),
            )
            conn.execute(
                "DELETE FROM global_task_worker_leases WHERE expires_at<=?", (now,)
            )
            conn.commit()
            return {"interrupted": cursor.rowcount}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def has_active(self, *, tenant_id: str | None = None) -> bool:
        conn = self._connect()
        try:
            if tenant_id is None:
                row = conn.execute(
                    "SELECT 1 FROM global_task_queue WHERE status IN ('queued','running') LIMIT 1"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM global_task_queue WHERE tenant_id=? "
                    "AND status IN ('queued','running') LIMIT 1", (tenant_id,),
                ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get(self, task_id: str) -> QueuedTask | None:
        conn = self._connect()
        try:
            return self._decode(conn.execute(
                "SELECT q.*, a.action AS resolution_action, a.actor AS resolution_actor, "
                "a.note AS resolution_note, a.acted_at AS resolution_at "
                "FROM global_task_queue q LEFT JOIN global_task_actions a ON a.task_id=q.task_id "
                "WHERE q.task_id=?", (task_id,)
            ).fetchone())
        finally:
            conn.close()

    def list_admin_audit(self, task_id: str) -> list[dict[str, Any]]:
        """Return append-only operator actions for a task, newest first."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, task_id, action, actor, acted_at, details_json "
                "FROM global_task_admin_audit WHERE task_id=? ORDER BY acted_at DESC, id DESC",
                (task_id,),
            ).fetchall()
            result = []
            for row in rows:
                try:
                    details = json.loads(row["details_json"] or "{}")
                except (TypeError, ValueError):
                    details = {}
                result.append({
                    "id": int(row["id"]), "task_id": row["task_id"],
                    "action": row["action"], "actor": row["actor"],
                    "acted_at": row["acted_at"], "details": details,
                })
            return result
        finally:
            conn.close()

    def resolve_failed_task(
        self, task_id: str, *, action: str, actor: str, note: str,
    ) -> tuple[dict[str, str], bool]:
        """Close a failed-task todo without rewriting the immutable execution result."""
        if action not in {"handled", "ignored"}:
            raise ValueError("action 必须是 handled 或 ignored")
        actor = str(actor or "").strip()
        note = str(note or "").strip()
        if not actor or len(actor) > 100:
            raise ValueError("actor 无效")
        if len(note) < 8 or len(note) > 500:
            raise ValueError("note 长度必须为 8 到 500 个字符")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                "SELECT status FROM global_task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None:
                raise TaskQueueError("任务不存在")
            if task["status"] not in ("failed", "interrupted"):
                raise TaskQueueError("只有 failed/interrupted 任务可以处理")
            existing = conn.execute(
                "SELECT action, actor, note, acted_at FROM global_task_actions WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if existing is not None:
                conn.commit()
                if existing["action"] != action:
                    raise TaskQueueError("该任务已完成其他处理，不允许反向修改")
                return dict(existing), False
            acted_at = _stamp()
            conn.execute(
                "INSERT INTO global_task_actions(task_id,action,actor,note,acted_at) VALUES(?,?,?,?,?)",
                (task_id, action, actor, note, acted_at),
            )
            conn.execute(
                "INSERT INTO global_task_admin_audit(task_id,action,actor,acted_at,details_json) "
                "VALUES(?,?,?,?,?)",
                (task_id, f"resolve_{action}", actor, acted_at,
                 json.dumps({"note": note}, ensure_ascii=False)),
            )
            conn.commit()
            return {"action": action, "actor": actor, "note": note, "acted_at": acted_at}, True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def retry_after_manual_verification(
        self, task_id: str, *, confirmed_by: str, confirmation_note: str,
        confirmed_not_executed: bool,
    ) -> tuple[QueuedTask, bool]:
        """Create one retry child only after an operator confirms no platform side effect occurred.

        The original row is immutable history. The child copies its tenant, task type,
        aggregate and exact payload snapshot; a unique parent link makes the request idempotent.
        """
        actor = str(confirmed_by or "").strip()
        note = str(confirmation_note or "").strip()
        if not confirmed_not_executed:
            raise ValueError("必须明确确认平台侧未执行，状态不明时禁止恢复")
        if not actor or len(actor) > 100:
            raise ValueError("confirmed_by 无效")
        if len(note) < 8 or len(note) > 500:
            raise ValueError("confirmation_note 长度必须为 8 到 500 个字符")

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            parent = conn.execute(
                "SELECT * FROM global_task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
            if parent is None:
                raise TaskQueueError("原任务不存在")
            if parent["status"] not in ("failed", "interrupted"):
                raise TaskQueueError("只有 failed/interrupted 任务可以人工恢复")
            resolved = conn.execute(
                "SELECT 1 FROM global_task_actions WHERE task_id=?", (task_id,)
            ).fetchone()
            if resolved is not None:
                raise TaskQueueError("该任务已被标记处理或忽略，不能再次恢复")
            existing = conn.execute(
                "SELECT * FROM global_task_queue WHERE retry_of_task_id=?", (task_id,)
            ).fetchone()
            if existing is not None:
                conn.commit()
                task = self._decode(existing)
                assert task is not None
                return task, False

            if parent["aggregate_id"] is not None:
                active = conn.execute(
                    "SELECT task_id FROM global_task_queue WHERE tenant_id=? AND task_type=? "
                    "AND aggregate_id=? AND status IN ('queued','running') LIMIT 1",
                    (parent["tenant_id"], parent["task_type"], parent["aggregate_id"]),
                ).fetchone()
                if active is not None:
                    raise TaskQueueError("相同业务对象已有活动任务，拒绝重复恢复")

            now = _stamp()
            queue_order = int(conn.execute(
                "SELECT COALESCE(MAX(queue_order), 0) + 1 FROM global_task_queue"
            ).fetchone()[0])
            retry_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO global_task_queue("
                "queue_order,task_id,task_type,tenant_id,aggregate_id,payload_json,available_at,"
                "status,created_at,max_attempts,retry_of_task_id,retry_confirmed_by,"
                "retry_confirmation_note) VALUES(?,?,?,?,?,?,?,'queued',?,1,?,?,?)",
                (
                    queue_order, retry_id, parent["task_type"], parent["tenant_id"],
                    parent["aggregate_id"], parent["payload_json"], now, now,
                    task_id, actor, note,
                ),
            )
            row = conn.execute(
                "SELECT * FROM global_task_queue WHERE task_id=?", (retry_id,)
            ).fetchone()
            conn.execute(
                "INSERT INTO global_task_admin_audit(task_id,action,actor,acted_at,details_json) "
                "VALUES(?,?,?,?,?)",
                (task_id, "retry", actor, now,
                 json.dumps({"retry_task_id": retry_id, "confirmed_not_executed": True,
                             "confirmation_note": note}, ensure_ascii=False)),
            )
            conn.commit()
            task = self._decode(row)
            assert task is not None
            return task, True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def find_latest(
        self, *, tenant_id: str, task_type: str, aggregate_id: str,
    ) -> QueuedTask | None:
        conn = self._connect()
        try:
            return self._decode(conn.execute(
                "SELECT * FROM global_task_queue WHERE tenant_id=? AND task_type=? "
                "AND aggregate_id=? ORDER BY queue_order DESC LIMIT 1",
                (tenant_id, task_type, str(aggregate_id)),
            ).fetchone())
        finally:
            conn.close()

    def list_running_tasks(self) -> list[QueuedTask]:
        """Return every running task in one SQLite read snapshot for watchdog enforcement."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT q.*, a.action AS resolution_action, a.actor AS resolution_actor, "
                "a.note AS resolution_note, a.acted_at AS resolution_at "
                "FROM global_task_queue q LEFT JOIN global_task_actions a ON a.task_id=q.task_id "
                "WHERE q.status='running' ORDER BY q.queue_order"
            ).fetchall()
            return [self._decode(row) for row in rows]
        finally:
            conn.close()

    def list_page(
        self, *, page: int = 1, page_size: int = 20,
        scope: str = "history", tenant_id: str | None = None,
        task_type: str | None = None, error_keyword: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
        unresolved_only: bool = False, status: str | None = None,
    ) -> tuple[list[QueuedTask], int]:
        """Return a stable, database-paginated slice of active or historical tasks."""
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 500))
        if scope not in {"active", "history", "failed", "filtered"}:
            raise ValueError("scope must be active, history, failed, or filtered")
        statuses = (
            ("queued", "running") if scope == "active"
            else ("failed", "interrupted") if scope == "failed"
            else ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")
            if scope == "filtered"
            else ("succeeded", "failed", "cancelled", "interrupted")
        )
        placeholders = ",".join("?" for _ in statuses)
        where = f"q.status IN ({placeholders})"
        params: list[object] = list(statuses)
        if status is not None:
            if status not in {"queued", "running", "succeeded", "failed", "cancelled", "interrupted"}:
                raise ValueError("status is invalid")
            where += " AND q.status=?"
            params.append(status)
        if unresolved_only:
            if scope != "failed":
                raise ValueError("unresolved_only 仅适用于 failed scope")
            where += " AND NOT EXISTS (SELECT 1 FROM global_task_actions a WHERE a.task_id=q.task_id)"

        if tenant_id is not None:
            where += " AND q.tenant_id=?"
            params.append(tenant_id)
        if task_type is not None:
            if not task_type or len(task_type) > 80:
                raise ValueError("task_type is invalid")
            where += " AND q.task_type=?"
            params.append(task_type)
        if error_keyword is not None:
            keyword = error_keyword.strip()
            if not keyword or len(keyword) > 120:
                raise ValueError("error_keyword is invalid")
            where += " AND COALESCE(q.error_message, '') LIKE ?"
            params.append(f"%{keyword}%")
        if date_from is not None:
            where += " AND substr(q.created_at, 1, 10) >= ?"
            params.append(date_from)
        if date_to is not None:
            where += " AND substr(q.created_at, 1, 10) <= ?"
            params.append(date_to)
        # Validate calendar dates, not merely their lexical ordering. Invalid values
        # otherwise produce silently empty result sets through SQLite substr().
        for field_name, date_value in (("date_from", date_from), ("date_to", date_to)):
            if date_value is not None:
                try:
                    parsed_date = datetime.strptime(date_value, "%Y-%m-%d")
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date") from exc
                if parsed_date.strftime("%Y-%m-%d") != date_value:
                    raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date")
        if date_from and date_to and date_from > date_to:
            raise ValueError("date_from cannot be later than date_to")
        offset = (page - 1) * page_size
        conn = self._connect()
        try:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS n FROM global_task_queue q WHERE {where}", params
            ).fetchone()
            rows = conn.execute(
                f"SELECT q.*, a.action AS resolution_action, a.actor AS resolution_actor, "
                f"a.note AS resolution_note, a.acted_at AS resolution_at "
                f"FROM global_task_queue q LEFT JOIN global_task_actions a ON a.task_id=q.task_id "
                f"WHERE {where} ORDER BY q.queue_order {'ASC' if scope == 'active' else 'DESC'} LIMIT ? OFFSET ?",
                (*params, page_size, offset),
            ).fetchall()
            tasks: list[QueuedTask] = []
            for row in rows:
                try:
                    task = self._decode(row)
                except TaskQueueError as exc:
                    # Never silently omit corrupt history rows: doing so makes the
                    # returned page disagree with COUNT(*) and hides persistent damage.
                    logger.exception("队列历史记录损坏 task_id=%s", row["task_id"])
                    raise TaskQueueError(
                        f"队列任务 {row['task_id']} 数据损坏，分页结果不完整"
                    ) from exc
                if task is not None:
                    tasks.append(task)
            return tasks, int(total_row["n"] if total_row else 0)
        finally:
            conn.close()

    def status_counts(self, *, scope: str = "active") -> dict:
        """Aggregate queue status counts globally and by tenant without loading task rows."""
        if scope == "active":
            statuses = ("queued", "running")
        elif scope == "failed":
            statuses = ("failed", "interrupted")
        elif scope == "history":
            statuses = ("succeeded", "failed", "cancelled", "interrupted")
        else:
            raise ValueError("scope must be active, failed, or history")
        placeholders = ",".join("?" for _ in statuses)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT tenant_id, status, COUNT(*) AS n FROM global_task_queue "
                f"WHERE status IN ({placeholders}) GROUP BY tenant_id, status",
                statuses,
            ).fetchall()
            by_status = {status: 0 for status in statuses}
            by_tenant: dict[str, dict[str, int]] = {}
            for row in rows:
                status = str(row["status"])
                tenant_id = str(row["tenant_id"])
                count = int(row["n"])
                by_status[status] = by_status.get(status, 0) + count
                by_tenant.setdefault(tenant_id, {})[status] = count
            return {
                "total": sum(by_status.values()),
                "by_status": by_status,
                "by_tenant": by_tenant,
            }
        finally:
            conn.close()

    def dispatch_summary(
        self, *, date_from: str | None = None, date_to: str | None = None,
        tenant_id: str | None = None, task_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Return bounded aggregate metrics and daily trends without loading task payloads."""
        from datetime import date, timedelta
        if date_from and date_to and date_from > date_to:
            raise ValueError("date_from cannot be later than date_to")
        where = ["1=1"]
        params: list[object] = []
        if date_from is not None:
            try:
                date.fromisoformat(date_from)
            except ValueError as exc:
                raise ValueError("date_from must be YYYY-MM-DD") from exc
            where.append("created_at >= ?")
            params.append(date_from)
        if date_to is not None:
            try:
                date_to_value = date.fromisoformat(date_to)
            except ValueError as exc:
                raise ValueError("date_to must be YYYY-MM-DD") from exc
            where.append("created_at < ?")
            params.append((date_to_value + timedelta(days=1)).isoformat())
        if tenant_id is not None:
            where.append("tenant_id = ?")
            params.append(tenant_id)
        if task_type is not None:
            if not task_type or len(task_type) > 80:
                raise ValueError("task_type is invalid")
            where.append("task_type = ?")
            params.append(task_type)
        allowed_statuses = {"queued", "running", "succeeded", "failed", "cancelled", "interrupted"}
        if status is not None:
            if status not in allowed_statuses:
                raise ValueError("status is invalid")
            where.append("status = ?")
            params.append(status)
        clause = " AND ".join(where)
        conn = self._connect()
        try:
            status_rows = conn.execute(
                f"SELECT status, COUNT(*) AS n FROM global_task_queue WHERE {clause} GROUP BY status",
                params,
            ).fetchall()
            statuses = {key: 0 for key in ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")}
            for row in status_rows:
                statuses[str(row["status"])] = int(row["n"])
            daily_rows = conn.execute(
                f"SELECT substr(created_at,1,10) AS day, "
                "SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded, "
                "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
                "COUNT(*) AS total "
                f"FROM global_task_queue WHERE {clause} GROUP BY substr(created_at,1,10) ORDER BY day",
                params,
            ).fetchall()
            duration = conn.execute(
                f"SELECT AVG((julianday(finished_at)-julianday(started_at))*86400.0) AS avg_seconds "
                f"FROM global_task_queue WHERE {clause} AND started_at IS NOT NULL "
                "AND finished_at IS NOT NULL AND status IN ('succeeded','failed','interrupted')",
                params,
            ).fetchone()
            return {
                "total": sum(statuses.values()),
                "by_status": statuses,
                "daily": [
                    {"date": str(row["day"]), "total": int(row["total"]),
                     "succeeded": int(row["succeeded"] or 0), "failed": int(row["failed"] or 0)}
                    for row in daily_rows
                ],
                "average_duration_seconds": (
                    round(float(duration["avg_seconds"]), 2)
                    if duration and duration["avg_seconds"] is not None else None
                ),
                "filters": {"date_from": date_from, "date_to": date_to,
                            "tenant_id": tenant_id, "task_type": task_type, "status": status},
            }
        finally:
            conn.close()

    def list(self, *, limit: int = 100, tenant_id: str | None = None) -> list[QueuedTask]:
        limit = max(1, min(int(limit), 500))
        conn = self._connect()
        try:
            if tenant_id is None:
                rows = conn.execute(
                    "SELECT * FROM global_task_queue "
                    "ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END, "
                    "queue_order DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM global_task_queue WHERE tenant_id=? "
                    "ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END, "
                    "queue_order DESC LIMIT ?", (tenant_id, limit),
                ).fetchall()
            tasks: list[QueuedTask] = []
            for row in rows:
                try:
                    task = self._decode(row)
                except TaskQueueError as exc:
                    logger.error(
                        "跳过无法解码的队列记录 task_id=%s error=%s",
                        row["task_id"], str(exc),
                    )
                    continue
                if task is not None:
                    tasks.append(task)
            return tasks
        finally:
            conn.close()
