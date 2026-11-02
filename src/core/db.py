"""
SQLite 连接管理 + schema 迁移。

设计要点：
1. SQLite 纯本地运行，不需要数据库服务器
2. 初始化执行 PRAGMA foreign_keys = ON（R16 外键约束生效）
3. schema_version 表追踪版本，启动时检查并执行增量迁移
4. 每次迁移前自动备份到 backups/（保留最近 10 份）
5. 全链路类型注解，Pydantic v2 边界校验

用法：
    from src.core.db import Database
    db = Database(cfg)
    db.initialize()
    conn = db.connect()
    conn.execute("SELECT * FROM goals")
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .timezone import business_now
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


def _restrict_sqlite_file_permissions(db_path: Path) -> None:
    """将 SQLite 主库及已存在的 WAL/SHM 辅助文件限制为当前用户可读写。"""
    for protected in (
        db_path,
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    ):
        if protected.exists():
            try:
                protected.chmod(0o600)
            except OSError:
                # 权限收紧失败需要告警，但不能把绝对路径或数据写入日志。
                logger.warning("无法收紧数据库文件权限：%s", protected.name)


# 迁移脚本目录
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


class Database:
    """SQLite 数据库管理器 — 连接、迁移、备份。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db_path = cfg.resolve_path(cfg.paths.db_file)
        self.backup_dir = cfg.resolve_path(cfg.paths.backup_dir)

    # ------------------------------------------------------------------
    # 基础连接
    # ------------------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """打开一个连接，启用外键、WAL 模式。"""
        conn = sqlite3.connect(
            str(self.db_path),
            # dict factory 让返回值可以像 dict 访问
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,  # 异步环境可能多线程访问
            timeout=30.0,  # SQLite busy handler；单位为秒
        )
        # 数据库包含用户内容、运营日志等私有数据；限制同机其他用户直接读取。
        try:
            self.db_path.chmod(0o600)
        except OSError:
            logger.warning("无法收紧数据库文件权限：%s", self.db_path.name)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")    # 读写并发
        conn.execute("PRAGMA synchronous = NORMAL;")    # WAL 下足够安全
        conn.execute("PRAGMA busy_timeout = 30000;")  # 忙等 30s
        _restrict_sqlite_file_permissions(self.db_path)
        conn.row_factory = sqlite3.Row                  # 列名访问
        return conn

    # ------------------------------------------------------------------
    # 迁移入口（启动时调用一次）
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """确保数据库存在，schema 版本对齐到最新。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # 确定当前版本
        current_version = self._get_current_version()
        logger.info("数据库当前 schema_version = %s（无版本 = 全新）", current_version)

        # 发现迁移脚本
        migrations = self._discover_migrations()
        # 不能用 MAX(version) 推断所有较小版本都已执行：并行分支晚合入的
        # 低编号迁移必须仍能执行。以 schema_version 中的实际记录为准。
        applied_versions = self._get_applied_versions()
        # 相同 SQL 内容只执行一次。历史上 027/030 存在逐字重复迁移，
        # 若只补齐缺失版本号，重复执行可能再次 ALTER/CREATE 并导致启动失败。
        seen_checksums: set[str] = set()
        pending: list[Migration] = []
        duplicate_aliases: list[Migration] = []
        for migration in migrations:
            checksum = hashlib.sha256(migration.path.read_bytes()).hexdigest()
            if migration.version in applied_versions:
                seen_checksums.add(checksum)
            elif checksum in seen_checksums:
                duplicate_aliases.append(migration)
            else:
                pending.append(migration)
                seen_checksums.add(checksum)

        if not pending and not duplicate_aliases:
            logger.info("数据库已对齐最新 schema，无需迁移")
            return

        # 逐个执行
        conn = self.connect()
        try:
            # 先备份
            if current_version > 0:
                self._backup()

            for mig in pending:
                if mig.name == "platform_account_singleton":
                    self._preflight_platform_account_singleton(conn)
                self._run_migration(conn, mig)

            for alias in duplicate_aliases:
                self._record_migration_alias(conn, alias)

            latest_version = max(
                [migration.version for migration in pending + duplicate_aliases],
                default=current_version,
            )
            logger.info("数据库迁移完成，最新版本 = %d", latest_version)
        except Exception:
            logger.exception("数据库迁移失败")
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _get_current_version(self) -> int:
        """读取 schema_version 表的最大版本；表不存在返回 0。"""
        if not self.db_path.exists():
            return 0
        conn = self.connect()
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            if cur.fetchone() is None:
                return 0
            row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    @staticmethod
    def _record_migration_alias(conn: sqlite3.Connection, mig: "Migration") -> None:
        """为内容完全重复的迁移登记版本号，但不重复执行 SQL。"""
        description = f"{mig.name} (duplicate migration content; already applied)"
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, description) VALUES (?, ?)",
                (int(mig.version), description),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        logger.info("迁移 %d 内容与已执行迁移相同，仅登记版本号", mig.version)

    def _get_applied_versions(self) -> set[int]:
        """读取实际已登记的迁移版本，避免较小编号的晚合入迁移被静默跳过。"""
        if not self.db_path.exists():
            return set()
        conn = self.connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if exists is None:
                return set()
            return {
                int(row[0]) for row in conn.execute(
                    "SELECT version FROM schema_version"
                ).fetchall()
            }
        finally:
            conn.close()

    def _discover_migrations(self) -> list["Migration"]:
        """扫描 migrations/*.sql，按文件名中的版本号排序。"""
        if not MIGRATIONS_DIR.exists():
            return []
        migrations: list[Migration] = []
        for f in MIGRATIONS_DIR.glob("*.sql"):
            name = f.stem  # 001_init
            parts = name.split("_", 1)
            if not parts or not parts[0].isdigit():
                continue
            version = int(parts[0])
            migrations.append(Migration(version=version, path=f, name=parts[1] if len(parts) > 1 else name))
        migrations.sort(key=lambda m: m.version)
        return migrations

    @staticmethod
    def _skip_existing_columns(conn: sqlite3.Connection, sql: str) -> str:
        """按完整语句（而非按行）跳过已存在列的 ADD COLUMN。

        ALTER 语句可能跨多行（例如列定义后换行写 CHECK 约束）；只删首行会留下孤立的
        续行导致语法错误，旧库升级时服务无法启动。
        """
        import re

        body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        pattern = re.compile(r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)", re.IGNORECASE)
        columns: dict[str, set[str]] = {}
        kept = []
        for statement in body.split(";"):
            if not statement.strip():
                continue
            match = pattern.match(statement)
            if match:
                table, column = match.group(1), match.group(2)
                if table not in columns:
                    columns[table] = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if column in columns[table]:
                    continue
            kept.append(statement.strip() + ";")
        return "\n".join(kept)

    @staticmethod
    def _preflight_platform_account_singleton(conn: sqlite3.Connection) -> None:
        """禁止自动丢弃历史账号：发现同平台重复登记时，给出可人工处理的明确错误。"""
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_accounts'"
        ).fetchone()
        if table is None:
            return
        duplicates = conn.execute(
            "SELECT platform, GROUP_CONCAT(id) AS account_ids, COUNT(*) AS total "
            "FROM platform_accounts GROUP BY platform HAVING COUNT(*) > 1 "
            "ORDER BY platform"
        ).fetchall()
        if duplicates:
            details = "；".join(
                f"{row['platform']}（记录 ID：{row['account_ids']}，共 {row['total']} 条）"
                for row in duplicates
            )
            raise RuntimeError(
                "账号组存在同平台多账号历史数据，数据库唯一约束未执行。"
                f"请先人工确认并删除/合并重复登记：{details}"
            )

    def _run_migration(self, conn: sqlite3.Connection, mig: "Migration") -> None:
        """执行单个迁移脚本并原子记录版本。"""
        sql = mig.path.read_text(encoding="utf-8")
        logger.info("执行迁移 %d — %s（%s）", mig.version, mig.name, mig.path)

        # 通用处理：跳过已存在列的 ALTER TABLE ADD COLUMN（兼容旧库升级）
        sql = self._skip_existing_columns(conn, sql)

        # sqlite3.executescript() 会先提交调用前的事务；因此必须把 BEGIN、迁移 SQL
        # 和 schema_version 写入放在同一脚本事务内，避免迁移中途失败留下半套 schema。
        description = mig.name.replace("'", "''")
        script = (
            "BEGIN IMMEDIATE;\n"
            + sql
            + f"\nINSERT INTO schema_version (version, description) "
              f"VALUES ({int(mig.version)}, '{description}');\n"
            + "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise

    def _backup(self) -> Path:
        """把当前 db 文件复制到 backups/easel_YYYYMMDD_HHMMSS.db。"""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.backup_dir.chmod(0o700)
        except OSError:
            logger.warning("无法收紧数据库备份目录权限：%s", self.backup_dir.name)
        ts = business_now(getattr(self, "cfg", None)).strftime("%Y%m%d_%H%M%S")
        dest = self.backup_dir / f"easel_{ts}.db"
        # WAL 模式下要连 journal 和 shm 一起备份
        for suffix in ("", "-wal", "-shm"):
            src = self.db_path.with_name(self.db_path.name + suffix)
            if src.exists():
                target = dest.with_name(dest.name + suffix)
                shutil.copy2(src, target)
                try:
                    target.chmod(0o600)
                except OSError:
                    logger.warning("无法收紧数据库备份文件权限：%s", target.name)
        logger.info("数据库已备份: %s", dest)

        # 保留最近 10 份
        self._prune_old_backups(keep=10)
        return dest

    def _prune_old_backups(self, keep: int = 10) -> None:
        """删除超过 keep 份的旧备份。"""
        backups = sorted(self.backup_dir.glob("easel_*.db"), reverse=True)
        for old in backups[keep:]:
            for suffix in ("", "-wal", "-shm"):
                target = old.with_name(old.name + suffix)
                if target.exists():
                    target.unlink()
            logger.info("清理旧备份: %s", old.name)

    # ------------------------------------------------------------------
    # 验证工具
    # ------------------------------------------------------------------

    def verify_schema(self) -> dict:
        """返回各表的 CHECK 约束是否完整（R7/R14 验证用）。"""
        conn = self.connect()
        try:
            result: dict = {}
            # 检查表是否存在
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            existing = {row[0] for row in cur.fetchall()}

            expected_tables = {
                "schema_version", "goals", "spiral_rounds", "topic_index",
                "contents", "drafts", "metrics", "ops_logs",
                "circuit_breaker_events", "config_changes", "platform_insights",
                "notifications", "task_runs", "ai_calls", "user_override",
                "signature_history", "hot_topics", "plan_settings",
                "content_assets", "media_assets", "media_asset_usages",
                "media_generation_settings", "media_generation_jobs",
                "tenant_settings", "tenant_setting_versions", "tenant_setting_migrations",
                "ops_targets", "industry_presets",
                "visual_design_versions", "visual_jobs", "visual_brand_profiles",
            }
            result["missing_tables"] = sorted(expected_tables - existing)
            result["existing_tables"] = sorted(existing & expected_tables)

            # drafts 表 CHECK 中 expired 不能漏（R14）
            if "drafts" in existing:
                cur = conn.execute("SELECT sql FROM sqlite_master WHERE name='drafts'")
                ddl = cur.fetchone()[0] or ""
                result["drafts_has_expired"] = "'expired'" in ddl

            # spiral_rounds CHECK 有全部 9 态（R7）
            if "spiral_rounds" in existing:
                cur = conn.execute("SELECT sql FROM sqlite_master WHERE name='spiral_rounds'")
                ddl = cur.fetchone()[0] or ""
                result["rounds_has_all_9_statuses"] = all(
                    s in ddl for s in (
                        "created", "producing", "awaiting_publish",
                        "collecting_metrics", "analyzing",
                        "completed", "partial_completed", "cancelled", "failed",
                    )
                )

            # ops_logs.status 必须含 pending_verify（R16/R17 灰色地带恢复）
            if "ops_logs" in existing:
                cur = conn.execute("SELECT sql FROM sqlite_master WHERE name='ops_logs'")
                ddl = cur.fetchone()[0] or ""
                result["ops_logs_has_pending_verify"] = "pending_verify" in ddl

            return result
        finally:
            conn.close()


class Migration:
    """单个迁移脚本的元数据。"""

    def __init__(self, version: int, path: Path, name: str):
        self.version = version
        self.path = path
        self.name = name

    def __repr__(self) -> str:
        return f"<Migration v{self.version}: {self.name}>"


# ======================================================================
# 共享连接的线程安全包装
# ======================================================================
#
# Web 进程里所有同步路由在 FastAPI 线程池里并发执行，又共用 lifespan 建的一个
# sqlite3 连接（还有调度器线程、事件循环里的执行器）。sqlite3 的同一连接被多线程
# 交错使用游标时会出现 fetchone() 返回 None、IndexError、
# "Recursive use of cursors not allowed" 等随机错误 —— 前端表现为页面随机
# 「加载失败：500」。
#
# LockedConnection 用一把可重入锁把「执行 + 取完结果」包成原子操作：execute()
# 在锁内把结果全部取出，返回一个只读的结果游标，之后的 fetchone/fetchall/迭代
# 都不再触碰底层连接。SQLite 本身就是单写者，串行化对吞吐影响很小。

import threading as _threading


class _BufferedCursor:
    """execute() 的结果：结果集已在锁内全部取出。"""

    __slots__ = ("_rows", "_pos", "description", "lastrowid", "rowcount")

    def __init__(self, cur: sqlite3.Cursor):
        self.description = cur.description
        self._rows = cur.fetchall() if cur.description is not None else []
        self._pos = 0
        self.lastrowid = cur.lastrowid
        self.rowcount = cur.rowcount

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchmany(self, size: int = 1):
        rows = self._rows[self._pos:self._pos + size]
        self._pos += len(rows)
        return rows

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def close(self) -> None:
        self._rows = []


class _LockedCursor:
    """conn.cursor() 的替身：每次 execute 都走连接锁。"""

    def __init__(self, owner: "LockedConnection"):
        self._owner = owner
        self._res: Optional[_BufferedCursor] = None

    def execute(self, sql, params=()):
        self._res = self._owner.execute(sql, params)
        return self

    def executemany(self, sql, seq):
        self._res = self._owner.executemany(sql, seq)
        return self

    def __getattr__(self, name):
        if self._res is None:
            raise AttributeError(name)
        return getattr(self._res, name)

    def __iter__(self):
        return iter(self._res or [])

    def close(self) -> None:
        self._res = None


class LockedConnection:
    """线程安全的 sqlite3.Connection 代理，并将隐式事务锁保持到 commit/rollback。

    SQLite 在第一次 DML 后会开启隐式事务。若只在 execute() 单条语句期间加锁，
    另一个线程仍可能调用 commit() 提交前一个线程的半截业务写入。这里在事务开启时
    保留一层 RLock，直到事务结束；其他线程的 execute/commit/rollback 都会等待。
    对「先 SELECT 校验、再 UPDATE」的关键业务操作，仍应使用 with conn: 显式包住
    整个读-校验-写流程。
    """

    def __init__(self, conn: sqlite3.Connection):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_lock", _threading.RLock())
        object.__setattr__(self, "_transaction_owner", None)
        object.__setattr__(self, "_context_owner", None)
        object.__setattr__(self, "_context_depth", 0)
        object.__setattr__(self, "_rollback_only", False)

    @property
    def raw(self) -> sqlite3.Connection:
        return self._conn

    def _run_locked(self, operation):
        self._lock.acquire()
        owner = _threading.get_ident()
        retain_current = False
        release_retained = False
        try:
            try:
                return operation()
            except BaseException:
                # SQL 语句失败后即使业务层捕获异常，也不能让此前的半截写入被提交。
                if self._context_owner == owner and self._context_depth > 0:
                    object.__setattr__(self, "_rollback_only", True)
                elif self._transaction_owner == owner:
                    try:
                        self._conn.rollback()
                    finally:
                        if not self._conn.in_transaction:
                            object.__setattr__(self, "_transaction_owner", None)
                            release_retained = True
                raise
        finally:
            # 发生 SQL 异常时也必须依据真实连接状态决定是否继续持锁。
            try:
                in_transaction = self._conn.in_transaction
            except sqlite3.Error:
                in_transaction = False
            transaction_owner = self._transaction_owner
            if in_transaction:
                if transaction_owner is None:
                    object.__setattr__(self, "_transaction_owner", owner)
                    retain_current = True
                elif transaction_owner != owner:
                    # 正常情况下其他线程无法到达这里，因为事务持有 RLock。
                    raise RuntimeError("SQLite 事务连接所有权异常")
            elif transaction_owner == owner:
                object.__setattr__(self, "_transaction_owner", None)
                release_retained = True
            if release_retained:
                self._lock.release()
            if not retain_current:
                self._lock.release()

    def execute(self, sql, params=()):
        return self._run_locked(
            lambda: _BufferedCursor(self._conn.execute(sql, params))
        )

    def executemany(self, sql, seq):
        return self._run_locked(
            lambda: _BufferedCursor(self._conn.executemany(sql, seq))
        )

    def executescript(self, script):
        # sqlite3.executescript 会先提交已有事务；_run_locked 会在事务结束后释放保留锁。
        return self._run_locked(
            lambda: _BufferedCursor(self._conn.executescript(script))
        )

    def cursor(self):
        return _LockedCursor(self)

    def commit(self) -> None:
        owner = _threading.get_ident()
        if self._context_owner == owner and self._context_depth > 0:
            # 请求/业务事务中的 commit 延迟到最外层 with conn 正常退出。
            return
        self._lock.acquire()
        release_retained = False
        try:
            self._conn.commit()
            if not self._conn.in_transaction and self._transaction_owner == owner:
                object.__setattr__(self, "_transaction_owner", None)
                release_retained = True
        finally:
            if release_retained:
                self._lock.release()
            self._lock.release()

    def rollback(self) -> None:
        owner = _threading.get_ident()
        if self._context_owner == owner and self._context_depth > 0:
            object.__setattr__(self, "_rollback_only", True)
            return
        self._lock.acquire()
        release_retained = False
        try:
            self._conn.rollback()
            if not self._conn.in_transaction and self._transaction_owner == owner:
                object.__setattr__(self, "_transaction_owner", None)
                release_retained = True
        finally:
            if release_retained:
                self._lock.release()
            self._lock.release()

    @contextmanager
    def transaction(self, *, immediate: bool = True):
        """显式事务 API；事务期间独占连接，默认先取得 SQLite 写保留锁。"""
        self._lock.acquire()
        owner = _threading.get_ident()
        if self._conn.in_transaction or self._context_owner is not None:
            self._lock.release()
            raise RuntimeError(
                "LockedConnection.transaction 不支持隐式嵌套；"
                "请让最外层事务统一管理提交与回滚"
            )
        object.__setattr__(self, "_context_owner", owner)
        object.__setattr__(self, "_context_depth", 1)
        object.__setattr__(self, "_rollback_only", False)
        try:
            self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        except BaseException:
            object.__setattr__(self, "_context_owner", None)
            object.__setattr__(self, "_context_depth", 0)
            object.__setattr__(self, "_rollback_only", False)
            self._lock.release()
            raise
        try:
            yield self
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            self.__exit__(None, None, None)

    def close(self) -> None:
        self._lock.acquire()
        owner = _threading.get_ident()
        release_retained = self._transaction_owner == owner
        try:
            self._conn.close()
            if release_retained:
                object.__setattr__(self, "_transaction_owner", None)
        finally:
            if release_retained:
                self._lock.release()
            self._lock.release()

    def __enter__(self):
        self._lock.acquire()
        owner = _threading.get_ident()
        context_owner = self._context_owner
        if context_owner is None:
            object.__setattr__(self, "_context_owner", owner)
            object.__setattr__(self, "_context_depth", 1)
            object.__setattr__(self, "_rollback_only", False)
        elif context_owner == owner:
            object.__setattr__(self, "_context_depth", self._context_depth + 1)
        else:
            # 其他线程应被 RLock 阻塞，不能与当前上下文同时进入。
            self._lock.release()
            raise RuntimeError("SQLite 事务上下文所有权异常")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        owner = _threading.get_ident()
        if self._context_owner != owner or self._context_depth < 1:
            self._lock.release()
            raise RuntimeError("SQLite 事务上下文退出线程不匹配")

        if exc_type is not None:
            object.__setattr__(self, "_rollback_only", True)
        depth = self._context_depth - 1
        object.__setattr__(self, "_context_depth", depth)
        if depth > 0:
            # 内层上下文不提交；任一层失败都会让最外层整体回滚。
            self._lock.release()
            return False

        release_retained = False
        try:
            if self._rollback_only:
                self._conn.rollback()
            else:
                self._conn.commit()
        except BaseException:
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            try:
                if not self._conn.in_transaction and self._transaction_owner == owner:
                    object.__setattr__(self, "_transaction_owner", None)
                    release_retained = True
            except sqlite3.Error:
                pass
            object.__setattr__(self, "_context_owner", None)
            object.__setattr__(self, "_context_depth", 0)
            object.__setattr__(self, "_rollback_only", False)
            if release_retained:
                self._lock.release()
            self._lock.release()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)




@contextmanager
def connection_transaction(conn, *, immediate: bool = True):
    """兼容 LockedConnection 与原生 sqlite3.Connection 的事务上下文。"""
    transaction = getattr(conn, "transaction", None)
    if callable(transaction):
        if getattr(conn, "_context_owner", None) == _threading.get_ident():
            # 已处于同线程的外层事务上下文（例如同步写 API 的 TransactionalAPIRoute 请求级
            # `with conn`）：加入外层事务，由最外层统一提交/回滚；内层异常会把整体标为回滚。
            # 外层尚未开始写事务时立刻取得写保留锁，保证读-校验-写不被并发写打断。
            with conn:
                if not conn.in_transaction:
                    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield conn
            return
        with transaction(immediate=immediate):
            yield conn
        return

    if getattr(conn, "in_transaction", False):
        raise RuntimeError(
            "connection_transaction 不支持隐式嵌套；请让最外层事务统一管理提交与回滚"
        )
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
