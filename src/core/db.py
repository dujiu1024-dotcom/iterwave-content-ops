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
import time
from datetime import datetime
from pathlib import Path

from .timezone import business_now
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


def _iter_sql_statements(sql: str):
    """Yield complete SQLite statements, including multi-statement lines and triggers."""
    statement: list[str] = []
    for char in sql:
        statement.append(char)
        if char == ";":
            candidate = "".join(statement)
            if sqlite3.complete_statement(candidate):
                if candidate.strip():
                    yield candidate
                statement.clear()
    if statement:
        candidate = "".join(statement)
        if candidate.strip():
            yield candidate


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
        conn.execute("PRAGMA busy_timeout = 30000;")  # 忙等 30s
        # 切换 journal_mode 需要数据库级锁，SQLite 对该 PRAGMA 的锁冲突
        # 不总是遵守 busy_timeout。并发 Worker 首次启动时做有限重试。
        wal_deadline = time.monotonic() + 30.0
        while True:
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= wal_deadline:
                    conn.close()
                    raise
                time.sleep(0.05)
        conn.execute("PRAGMA synchronous = NORMAL;")    # WAL 下足够安全
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
        self._verify_applied_migration_checksums(migrations, applied_versions)
        # 相同 SQL 内容只执行一次。历史上 027/030 存在逐字重复迁移，
        # 若只补齐缺失版本号，重复执行可能再次 ALTER/CREATE 并导致启动失败。
        # 先收集所有已登记版本对应的内容摘要，而不是只看遍历时遇到的
        # 较早版本。旧库可能只登记了较高编号的重复迁移（例如 030），
        # 此时较低编号的 027 也必须作为别名补登记，不能再次执行 SQL。
        applied_checksums = {
            hashlib.sha256(migration.path.read_bytes()).hexdigest()
            for migration in migrations
            if migration.version in applied_versions
        }
        seen_checksums: set[str] = set(applied_checksums)
        pending: list[Migration] = []
        duplicate_aliases: list[Migration] = []
        for migration in migrations:
            checksum = hashlib.sha256(migration.path.read_bytes()).hexdigest()
            if migration.version in applied_versions:
                continue
            if checksum in seen_checksums:
                duplicate_aliases.append(migration)
            else:
                pending.append(migration)
                seen_checksums.add(checksum)

        if not pending and not duplicate_aliases:
            # A process may have stopped after creating the checksum table but
            # before completing the one-time baseline of older applied versions.
            if self.db_path.exists():
                baseline_conn = self.connect()
                try:
                    self._backfill_migration_checksums(baseline_conn, migrations)
                finally:
                    baseline_conn.close()
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

            self._backfill_migration_checksums(conn, migrations)

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
            checksum_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration_checksums'"
            ).fetchone()
            if checksum_table is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migration_checksums "
                    "(version, checksum, verification_status) VALUES (?, ?, 'alias')",
                    (int(mig.version), hashlib.sha256(mig.path.read_bytes()).hexdigest()),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        logger.info("迁移 %d 内容与已执行迁移相同，仅登记版本号", mig.version)

    def _verify_applied_migration_checksums(
        self, migrations: list["Migration"], applied_versions: set[int]
    ) -> None:
        """Fail closed when a previously checksummed migration file has drifted."""
        if not applied_versions or not self.db_path.exists():
            return
        conn = self.connect()
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration_checksums'"
            ).fetchone()
            if table is None:
                return
            stored = {
                int(row["version"]): row["checksum"]
                for row in conn.execute(
                    "SELECT version, checksum FROM schema_migration_checksums"
                ).fetchall()
            }
            for migration in migrations:
                if migration.version not in applied_versions:
                    continue
                expected = stored.get(int(migration.version))
                if expected is None:
                    continue  # Older applied migrations are baselined after migration 129.
                actual = hashlib.sha256(migration.path.read_bytes()).hexdigest()
                # A parallel historical branch briefly shipped media-generation
                # budget columns as migration 131. Accept only that exact checksum
                # for version 131; forward migration 135 restores master-plan fields.
                legacy_checksums = {
                    131: {"db83577353daffd3ebc4ce8707b544bdf3ddd6cfcc20fdf505f8e49e0d1dc4db"}
                }
                if actual != expected and expected not in legacy_checksums.get(migration.version, set()):
                    raise RuntimeError(
                        f"已应用迁移 {migration.version} ({migration.name}) 的 SQL 文件发生变化；"
                        "为避免静默 schema drift，已停止启动。请新增迁移，不要修改历史迁移文件。"
                    )
        finally:
            conn.close()

    @staticmethod
    def _backfill_migration_checksums(
        conn: sqlite3.Connection, migrations: list["Migration"]
    ) -> None:
        """Record a one-time baseline for migrations applied before checksum tracking existed."""
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration_checksums'"
        ).fetchone()
        if table is None:
            return
        applied = {
            int(row[0]) for row in conn.execute("SELECT version FROM schema_version").fetchall()
        }
        for migration in migrations:
            if migration.version not in applied:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO schema_migration_checksums "
                "(version, checksum, verification_status) VALUES (?, ?, 'baseline')",
                (int(migration.version), hashlib.sha256(migration.path.read_bytes()).hexdigest()),
            )
        conn.commit()

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
        versions: dict[int, str] = {}
        collisions: list[str] = []
        for migration in migrations:
            previous = versions.get(migration.version)
            if previous is not None:
                collisions.append(f"{migration.version}: {previous}, {migration.path.name}")
            else:
                versions[migration.version] = migration.path.name
        if collisions:
            raise RuntimeError(
                "发现重复的数据库迁移版本号，拒绝启动以避免静默跳过迁移："
                + "；".join(collisions)
            )
        return migrations

    @staticmethod
    def _skip_existing_columns(conn: sqlite3.Connection, sql: str) -> str:
        """Skip complete ALTER TABLE statements whose columns already exist.

        Use SQLite statement boundaries instead of splitting on semicolons;
        trigger bodies and quoted SQL strings may contain internal semicolons.
        """
        import re

        pattern = re.compile(
            r"^\s*ALTER\s+TABLE\s+([A-Za-z_]\w*)\s+ADD\s+COLUMN\s+([A-Za-z_]\w*)",
            re.IGNORECASE,
        )
        columns: dict[str, set[str]] = {}
        kept: list[str] = []

        def should_keep(candidate: str) -> bool:
            without_comments = re.sub(r"(?m)^\s*--.*?$", "", candidate)
            match = pattern.match(without_comments)
            if not match:
                return True
            table, column = match.groups()
            if table not in columns:
                columns[table] = {
                    row[1] for row in conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
            return column not in columns[table]

        for statement in _iter_sql_statements(sql):
            if should_keep(statement):
                kept.append(statement)
        return "".join(kept)

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
        """在 SQLite 写锁内重检版本并原子执行迁移，避免多进程重复执行。"""
        try:
            conn.execute("BEGIN IMMEDIATE")
            version_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if version_table is not None and conn.execute(
                "SELECT 1 FROM schema_version WHERE version = ?", (int(mig.version),)
            ).fetchone():
                conn.commit()
                logger.info("迁移 %d 已由其他进程完成，跳过", mig.version)
                return

            sql = mig.path.read_text(encoding="utf-8")
            logger.info("执行迁移 %d — %s（%s）", mig.version, mig.name, mig.path)
            # 必须在拿到写锁后读取列结构，避免另一个启动进程刚完成 ALTER。
            sql = self._skip_existing_columns(conn, sql)

            # 不使用 executescript：它会隐式提交当前事务并释放迁移写锁。
            # complete_statement 可正确处理多行 SQL 以及触发器内部分号。
            for statement in _iter_sql_statements(sql):
                conn.execute(statement)

            description = mig.name
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (int(mig.version), description),
            )
            checksum_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration_checksums'"
            ).fetchone()
            if checksum_table is not None:
                conn.execute(
                    "INSERT INTO schema_migration_checksums "
                    "(version, checksum, verification_status) VALUES (?, ?, 'applied')",
                    (int(mig.version), hashlib.sha256(mig.path.read_bytes()).hexdigest()),
                )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise

    def _backup(self) -> Path:
        """通过 SQLite backup API 创建单文件一致性快照，安全处理 WAL。"""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.backup_dir.chmod(0o700)
        except OSError:
            logger.warning("无法收紧数据库备份目录权限：%s", self.backup_dir.name)
        ts = business_now(getattr(self, "cfg", None)).strftime("%Y%m%d_%H%M%S_%f")
        # Concurrent application workers may take a startup backup in the same
        # second. A unique suffix prevents them sharing/truncating one temp file.
        unique = time.time_ns()
        dest = self.backup_dir / f"easel_{ts}_{unique}.db"
        temp = dest.with_name(dest.name + ".tmp")
        source = sqlite3.connect(str(self.db_path), timeout=30.0)
        target = sqlite3.connect(str(temp), timeout=30.0)
        try:
            source.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise sqlite3.DatabaseError("备份快照完整性检查失败")
            target.commit()
        except Exception:
            target.close()
            source.close()
            temp.unlink(missing_ok=True)
            raise
        else:
            target.close()
            source.close()
        try:
            temp.chmod(0o600)
        except OSError:
            logger.warning("无法收紧数据库备份文件权限：%s", temp.name)
        temp.replace(dest)
        logger.info("数据库一致性快照已备份: %s", dest)

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

    def verify_schema(self, full_integrity: bool = False) -> dict:
        """Audit required schema objects and SQLite integrity.

        quick_check is the inexpensive default for routine health checks.
        Set full_integrity=True during deployment/upgrade acceptance to run
        SQLite's full integrity_check.
        """
        conn = self.connect()
        try:
            result: dict = {}
            # 检查表是否存在
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            existing = {row[0] for row in cur.fetchall()}

            expected_tables = {
                "schema_version", "schema_migration_checksums", "goals", "spiral_rounds", "topic_index",
                "contents", "drafts", "metrics", "ops_logs",
                "circuit_breaker_events", "config_changes", "platform_insights",
                "notifications", "task_runs", "ai_calls", "user_override",
                "signature_history", "hot_topics", "plan_settings",
                "content_assets", "media_assets", "media_asset_usages",
                "original_assets", "original_asset_versions", "original_asset_links",
                "original_asset_analyses", "original_asset_gaps", "production_task_asset_usages",
                "media_generation_settings", "media_generation_jobs",
                "tenant_settings", "tenant_setting_versions", "tenant_setting_migrations",
                "ops_targets", "industry_presets",
                "visual_design_versions", "visual_jobs", "visual_brand_profiles",
                # Tables introduced by the activation, planning, skills and
                # draft-verification migrations (100–129).
                "activation_cohort", "sample_runs", "sample_contents",
                "content_events", "metric_snapshots", "activation_run_deletions",
                "weekly_action_feedback", "content_plans", "content_plan_drafts",
                "tenant_skill_registry", "content_plan_rounds",
                "hot_source_test_history", "content_template_versions",
                "industry_preset_versions", "master_plan_versions", "sub_plans",
                "sub_plan_versions", "sub_plan_stages", "round_open_idempotency",
                "tenant_skill_versions", "tenant_skill_audit",
                "tenant_skill_references", "tenant_skill_executions",
                "draft_save_verifications",
                "production_lines", "production_line_versions",
                # Tables introduced by earlier incremental migrations.
                "app_settings", "autogrow_runs", "orchestration_settings",
                "notify_rules", "platform_styles", "account_snapshots",
                "platform_accounts", "hot_topic_import_audit",
                # Durable review, strategy outcome and production-task tables
                # introduced by migrations 133, 136 and 137.
                "content_review_events", "strategy_effect_reviews",
                "sub_plan_stage_outcomes", "production_tasks",
                "production_task_attempts", "strategy_step_condition_reviews",
            }
            result["missing_tables"] = sorted(expected_tables - existing)
            result["existing_tables"] = sorted(existing & expected_tables)

            known_migration_versions = {
                migration.version for migration in self._discover_migrations()
            }
            if "schema_version" in existing:
                applied_migration_versions = {
                    int(row[0]) for row in conn.execute(
                        "SELECT version FROM schema_version"
                    ).fetchall()
                }
            else:
                applied_migration_versions = set()
            result["unapplied_migrations"] = sorted(
                known_migration_versions - applied_migration_versions
            )
            result["unknown_applied_migrations"] = sorted(
                applied_migration_versions - known_migration_versions
            )

            expected_views = {"sub_plan_stage_publish_stats"}
            existing_views = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = ?", ("view",)
                ).fetchall()
            }
            result["missing_views"] = sorted(expected_views - existing_views)
            result["existing_views"] = sorted(expected_views & existing_views)

            expected_indexes = {
                # Core schema and migrations 001–034.
                "idx_drafts_status", "idx_drafts_platform", "idx_metrics_content",
                "idx_metrics_fetch", "idx_ops_logs_platform", "idx_task_runs_status",
                "idx_task_runs_biz", "idx_topic_dimension", "idx_rounds_goal",
                "idx_metrics_platform", "idx_drafts_saved_at", "idx_circuit_breaker_platform",
                "idx_notifications_sent", "idx_contents_round", "idx_drafts_content",
                "idx_signature_history_created", "idx_content_assets_type",
                "idx_ops_logs_action_day", "idx_hot_topics_match", "idx_hot_topics_last_seen",
                "idx_hot_topics_dup", "idx_autogrow_runs_at", "idx_topic_index_excluded",
                "ux_notify_rules_scenario_channel", "idx_account_snapshots_platform_time",
                "idx_media_assets_status_created", "idx_media_assets_sha256",
                "idx_media_asset_usages_asset", "idx_media_asset_usages_content",
                "idx_media_generation_jobs_created", "idx_platform_accounts_platform_status",
                "idx_tenant_setting_versions_version", "idx_ops_targets_dispatch",
                "idx_industry_presets_builtin", "idx_hot_topic_import_audit_topic",
                # Activation, planning, skills and visual migrations 100–129.
                "idx_sample_runs_tenant_created", "ux_sample_runs_one_active_per_tenant",
                "idx_sample_contents_run", "idx_sample_contents_tenant",
                "idx_content_events_tenant_time", "idx_content_events_type_time",
                "idx_content_events_idempotency", "idx_metric_snapshots_content_time",
                "idx_metric_snapshots_source_time", "idx_activation_run_deletions_tenant_time",
                "idx_weekly_action_feedback_tenant_period", "ux_sample_contents_promoted_content",
                "uq_platform_accounts_one_per_platform", "idx_content_plans_date",
                "idx_content_plans_status", "idx_content_plan_drafts_draft",
                "idx_tenant_skill_enabled", "idx_content_plan_rounds_plan",
                "idx_content_plan_rounds_task", "idx_contents_template_id",
                "idx_notifications_read_sent", "idx_ops_logs_task_run_id",
                "idx_notifications_task_run_id", "idx_notifications_ops_log_id",
                "idx_notifications_round_id", "idx_notifications_draft_id",
                "idx_hot_source_test_history_key_id", "idx_hot_source_test_history_tested_at",
                "idx_industry_preset_versions_key", "idx_master_plan_one_effective",
                "idx_master_plan_signature", "idx_master_plan_created",
                "idx_round_master_plan_version", "idx_round_sub_plan_version",
                "idx_round_plan_attribution", "idx_sub_plans_master_platform",
                "idx_sub_plans_status", "idx_sub_plan_one_effective_version",
                "idx_sub_plan_versions_master", "idx_sub_plan_versions_created",
                "idx_sub_plan_stages_version", "idx_sub_plan_stages_status",
                "idx_round_open_idempotency_status", "idx_round_open_idempotency_stage",
                "idx_sub_plan_stages_stage_key", "uq_sub_plan_stages_version_key",
                "idx_visual_design_versions_content", "idx_visual_design_versions_status",
                "idx_visual_jobs_content", "idx_visual_jobs_queue",
                "idx_visual_jobs_version_page",
                "idx_skill_audit_skill_time", "idx_skill_executions_skill_time",
                "idx_draft_save_verifications_status",
                # Review, strategy outcome and production task indexes (133/136/137).
                "idx_drafts_review_status", "idx_drafts_publish_status",
                "idx_content_review_events_content", "idx_content_review_events_round",
                "idx_strategy_effect_reviews_stage",
                "idx_sub_plan_stage_outcomes_lookup", "idx_sub_plan_stage_outcomes_version",
                "idx_production_tasks_round_status", "idx_production_tasks_step_status",
                "idx_production_tasks_recovery", "idx_production_task_attempts_task",
                "idx_contents_production_task", "idx_production_tasks_content",
                # Production-line lifecycle and immutable version indexes (139).
                "idx_production_lines_status", "idx_production_line_versions_subplan",
                "idx_production_line_versions_line", "idx_spiral_rounds_production_line",
                "idx_production_tasks_line_version", "idx_production_lines_current_version",
                "idx_production_line_versions_based_on",
                "idx_strategy_step_condition_reviews_stage",
                # Foreign-key child indexes added in migration 128.
                "idx_ops_logs_round_id", "idx_contents_topic_id",
                "idx_user_override_round_id",
                "idx_user_override_ai_call_id",
                "idx_signature_history_round_id", "idx_autogrow_runs_round_id",
                "idx_content_events_sample_run", "idx_content_events_sample_content",
                "idx_metric_snapshots_draft_time", "idx_metric_snapshots_content_id_time",
                "idx_metric_snapshots_correction_of", "idx_media_generation_jobs_asset",
                "idx_master_plan_based_on_version", "idx_sub_plan_versions_based_on_version",
                "idx_round_open_master_version", "idx_round_open_sub_plan_version",
                "idx_round_open_sub_plan_stage",
            }
            existing_indexes = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE ?",
                    ("index", "sqlite_%"),
                ).fetchall()
            }
            result["missing_indexes"] = sorted(expected_indexes - existing_indexes)
            result["existing_indexes"] = sorted(expected_indexes & existing_indexes)

            expected_triggers = {
                "trg_production_tasks_validate_insert",
                "trg_production_tasks_validate_update",
                "trg_production_lines_validate_current_version_insert",
                "trg_production_lines_validate_current_version_update",
                "trg_production_line_versions_validate_insert",
                "trg_production_line_versions_validate_update",
                "trg_contents_task_link_validate_insert",
                "trg_contents_task_link_validate_update",
                "trg_production_tasks_sync_content_insert",
                "trg_production_tasks_sync_content_update",
                "trg_contents_sync_production_task_insert",
                "trg_contents_sync_production_task_update",
                "trg_contents_prevent_linked_delete",
                "trg_production_tasks_prevent_linked_delete",
                "trg_production_tasks_prevent_content_relink",
                "trg_contents_prevent_task_relink",
                "trg_rounds_prevent_task_orphan",
                "trg_master_plan_versions_prevent_task_orphan",
                "trg_sub_plans_prevent_task_orphan",
                "trg_sub_plan_versions_prevent_task_orphan",
                "trg_sub_plan_stages_prevent_task_orphan",
                "trg_sub_plan_stages_prevent_task_rekey",
                "trg_production_line_versions_prevent_task_orphan",
            }
            existing_triggers = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
            result["missing_triggers"] = sorted(expected_triggers - existing_triggers)
            result["existing_triggers"] = sorted(expected_triggers & existing_triggers)

            expected_foreign_keys = {
                ("schema_migration_checksums", "version", "schema_version", "version", "CASCADE"),
                ("spiral_rounds", "goal_id", "goals", "id", "NO ACTION"),
                ("contents", "round_id", "spiral_rounds", "id", "NO ACTION"),
                ("contents", "topic_id", "topic_index", "id", "NO ACTION"),
                ("drafts", "content_id", "contents", "id", "NO ACTION"),
                ("metrics", "draft_id", "drafts", "id", "NO ACTION"),
                ("metrics", "content_id", "contents", "id", "NO ACTION"),
                ("ops_logs", "round_id", "spiral_rounds", "id", "NO ACTION"),
                ("platform_insights", "round_id", "spiral_rounds", "id", "NO ACTION"),
                ("user_override", "round_id", "spiral_rounds", "id", "NO ACTION"),
                ("user_override", "ai_call_id", "ai_calls", "id", "NO ACTION"),
                ("signature_history", "round_id", "spiral_rounds", "id", "NO ACTION"),
                ("autogrow_runs", "round_id", "spiral_rounds", "id", "NO ACTION"),
                ("media_asset_usages", "media_asset_id", "media_assets", "id", "RESTRICT"),
                ("media_asset_usages", "content_id", "contents", "id", "SET NULL"),
                ("media_generation_jobs", "output_asset_id", "media_assets", "id", "SET NULL"),
                ("sample_contents", "sample_run_id", "sample_runs", "id", "CASCADE"),
                ("content_events", "sample_run_id", "sample_runs", "id", "CASCADE"),
                ("content_events", "sample_content_id", "sample_contents", "id", "CASCADE"),
                ("metric_snapshots", "draft_id", "drafts", "id", "NO ACTION"),
                ("metric_snapshots", "content_id", "contents", "id", "NO ACTION"),
                ("metric_snapshots", "correction_of", "metric_snapshots", "id", "NO ACTION"),
                ("activation_run_deletions", "sample_run_id", "sample_runs", "id", "NO ACTION"),
                ("sample_contents", "promoted_content_id", "contents", "id", "NO ACTION"),
                ("content_plan_drafts", "plan_id", "content_plans", "id", "CASCADE"),
                ("content_plan_rounds", "plan_id", "content_plans", "id", "CASCADE"),
                ("contents", "template_id", "content_assets", "id", "SET NULL"),
                ("ops_logs", "task_run_id", "task_runs", "id", "NO ACTION"),
                ("notifications", "task_run_id", "task_runs", "id", "NO ACTION"),
                ("notifications", "ops_log_id", "ops_logs", "id", "NO ACTION"),
                ("notifications", "round_id", "spiral_rounds", "id", "NO ACTION"),
                ("notifications", "draft_id", "drafts", "id", "NO ACTION"),
                ("content_template_versions", "template_id", "content_assets", "id", "RESTRICT"),
                ("master_plan_versions", "legacy_history_id", "signature_history", "id", "NO ACTION"),
                ("master_plan_versions", "based_on_version_id", "master_plan_versions", "id", "NO ACTION"),
                ("sub_plans", "master_plan_version_id", "master_plan_versions", "id", "NO ACTION"),
                ("sub_plan_versions", "sub_plan_id", "sub_plans", "id", "RESTRICT"),
                ("sub_plan_versions", "master_plan_version_id", "master_plan_versions", "id", "NO ACTION"),
                ("sub_plan_versions", "based_on_version_id", "sub_plan_versions", "id", "NO ACTION"),
                ("sub_plan_stages", "sub_plan_version_id", "sub_plan_versions", "id", "RESTRICT"),
                ("production_lines", "sub_plan_id", "sub_plans", "id", "RESTRICT"),
                ("production_lines", "current_version_id", "production_line_versions", "id", "RESTRICT"),
                ("production_line_versions", "production_line_id", "production_lines", "id", "RESTRICT"),
                ("production_line_versions", "sub_plan_version_id", "sub_plan_versions", "id", "RESTRICT"),
                ("production_line_versions", "based_on_version_id", "production_line_versions", "id", "RESTRICT"),
                ("spiral_rounds", "production_line_version_id", "production_line_versions", "id", "RESTRICT"),
                ("production_tasks", "production_line_version_id", "production_line_versions", "id", "RESTRICT"),
                ("round_open_idempotency", "master_plan_version_id", "master_plan_versions", "id", "NO ACTION"),
                ("round_open_idempotency", "sub_plan_id", "sub_plans", "id", "NO ACTION"),
                ("round_open_idempotency", "sub_plan_version_id", "sub_plan_versions", "id", "NO ACTION"),
                ("round_open_idempotency", "sub_plan_stage_id", "sub_plan_stages", "id", "NO ACTION"),
                ("visual_design_versions", "content_id", "contents", "id", "CASCADE"),
                ("visual_jobs", "content_id", "contents", "id", "CASCADE"),
                ("visual_jobs", "visual_version_id", "visual_design_versions", "id", "SET NULL"),
                ("tenant_skill_references", "skill_id", "tenant_skill_registry", "skill_id", "CASCADE"),
                ("draft_save_verifications", "draft_id", "drafts", "id", "CASCADE"),
                ("content_review_events", "content_id", "contents", "id", "RESTRICT"),
                ("content_review_events", "draft_id", "drafts", "id", "SET NULL"),
                ("content_review_events", "round_id", "spiral_rounds", "id", "SET NULL"),
                ("strategy_effect_reviews", "stage_id", "sub_plan_stages", "id", "RESTRICT"),
                ("sub_plan_stage_outcomes", "sub_plan_id", "sub_plans", "id", "RESTRICT"),
                ("sub_plan_stage_outcomes", "source_version_id", "sub_plan_versions", "id", "RESTRICT"),
                ("sub_plan_stage_outcomes", "stage_id", "sub_plan_stages", "id", "RESTRICT"),
                ("production_task_attempts", "task_id", "production_tasks", "id", "CASCADE"),
                ("strategy_step_condition_reviews", "stage_id", "sub_plan_stages", "id", "RESTRICT"),
            }
            actual_foreign_keys = set()
            for table_name in existing:
                escaped_table = table_name.replace('"', '""')
                for fk in conn.execute(f'PRAGMA foreign_key_list("{escaped_table}")').fetchall():
                    actual_foreign_keys.add((
                        table_name, fk["from"], fk["table"], fk["to"], fk["on_delete"].upper()
                    ))
            result["missing_foreign_keys"] = sorted(expected_foreign_keys - actual_foreign_keys)

            expected_unique_constraints = {
                ("sample_runs", ("tenant_id", "idempotency_key")),
                ("contents", ("round_id", "topic_id", "platform")),
                ("drafts", ("content_id", "platform")),
                ("metrics", ("draft_id", "fetched_at")),
                ("platform_insights", ("round_id", "platform")),
                ("notifications", ("type", "platform", "sent_at")),
                ("media_assets", ("stored_filename",)),
                ("tenant_setting_versions", ("version",)),
                ("platform_accounts", ("platform", "normalized_handle")),
                ("notify_rules", ("scenario", "channel")),
                ("sample_runs", ("tenant_id",)),
                ("sample_contents", ("promoted_content_id",)),
                ("content_events", ("tenant_id", "idempotency_key")),
                ("platform_accounts", ("platform",)),
                ("master_plan_versions", ("status",)),
                ("sub_plan_versions", ("sub_plan_id",)),
                ("sub_plan_stages", ("sub_plan_version_id", "stage_key")),
                ("content_plan_drafts", ("plan_id", "draft_id")),
                ("content_plan_rounds", ("plan_id", "round_id")),
                ("content_plan_rounds", ("round_id",)),
                ("content_template_versions", ("template_id", "version")),
                ("master_plan_versions", ("version_no",)),
                ("master_plan_versions", ("legacy_history_id",)),
                ("sub_plan_versions", ("sub_plan_id", "version_no")),
                ("sub_plan_stages", ("sub_plan_version_id", "stage_order")),
                ("round_open_idempotency", ("idempotency_key",)),
                ("round_open_idempotency", ("round_id",)),
                ("visual_design_versions", ("content_id", "version")),
                ("tenant_skill_versions", ("skill_id", "version")),
                ("tenant_skill_references", ("skill_id", "reference_type", "reference_id")),
                ("draft_save_verifications", ("draft_id",)),
                ("schema_migration_checksums", ("version",)),
                ("production_tasks", ("idempotency_key",)),
                ("production_task_attempts", ("task_id", "attempt_no")),
                ("production_lines", ("sub_plan_id",)),
                ("production_line_versions", ("production_line_id", "version_no")),
            }
            actual_unique_constraints = set()
            for table_name in existing:
                escaped_table = table_name.replace('"', '""')
                table_info = conn.execute(
                    f'PRAGMA table_info("{escaped_table}")'
                ).fetchall()
                primary_key_columns = tuple(
                    row["name"] for row in sorted(
                        (column for column in table_info if int(column["pk"]) > 0),
                        key=lambda column: int(column["pk"]),
                    )
                )
                if primary_key_columns:
                    # INTEGER PRIMARY KEY aliases rowid and has no sqlite_autoindex.
                    actual_unique_constraints.add((table_name, primary_key_columns))
                indexes = conn.execute(f'PRAGMA index_list("{escaped_table}")').fetchall()
                for index in indexes:
                    if not index["unique"]:
                        continue
                    escaped_index = index["name"].replace('"', '""')
                    columns = tuple(
                        row["name"] for row in conn.execute(
                            f'PRAGMA index_info("{escaped_index}")'
                        ).fetchall()
                    )
                    actual_unique_constraints.add((table_name, columns))
            result["missing_unique_constraints"] = sorted(
                expected_unique_constraints - actual_unique_constraints
            )

            # Additive schema contract for the recent business migrations.
            expected_columns = {
                "drafts": {
                    "publish_status": "TEXT", "published_at": "TEXT",
                    "publish_result_note": "TEXT", "publish_time_source": "TEXT",
                    "review_status": "TEXT",
                },
                "contents": {"production_task_id": "TEXT"},
                "sub_plan_stages": {
                    "strategy_goal": "TEXT", "current_problem": "TEXT",
                    "execution_strategy": "TEXT", "prerequisites": "TEXT",
                    "input_info": "TEXT", "expected_result": "TEXT",
                    "completion_criteria": "TEXT", "effect_evaluation": "TEXT",
                    "entry_condition": "TEXT", "entry_condition_met": "INTEGER",
                    "dependencies_json": "TEXT",
                    "business_completion_required": "INTEGER",
                    "business_completion_status": "TEXT",
                    "effect_required": "INTEGER", "effect_status": "TEXT",
                },
                "content_review_events": {
                    "id": "INTEGER", "content_id": "TEXT", "draft_id": "TEXT",
                    "round_id": "INTEGER", "event_type": "TEXT", "actor": "TEXT",
                    "reason": "TEXT", "before_json": "TEXT", "after_json": "TEXT",
                    "created_at": "TEXT",
                },
                "strategy_effect_reviews": {
                    "id": "INTEGER", "stage_id": "INTEGER", "assessment": "TEXT",
                    "recommendation": "TEXT", "evidence_note": "TEXT",
                    "confidence": "TEXT", "evidence_json": "TEXT",
                    "created_by": "TEXT", "created_at": "TEXT",
                },
                "sub_plan_stage_outcomes": {
                    "id": "INTEGER", "sub_plan_id": "TEXT", "stage_key": "TEXT",
                    "source_version_id": "INTEGER", "stage_id": "INTEGER",
                    "result_json": "TEXT", "evaluation": "TEXT",
                    "recorded_at": "TEXT", "recorded_by": "TEXT",
                },
                "spiral_rounds": {"production_line_version_id": "INTEGER"},
                "production_lines": {
                    "id": "TEXT", "sub_plan_id": "TEXT", "status": "TEXT",
                    "current_version_id": "INTEGER", "created_at": "TEXT",
                    "updated_at": "TEXT", "created_by": "TEXT",
                    "activated_at": "TEXT", "activated_by": "TEXT", "pause_reason": "TEXT",
                },
                "production_line_versions": {
                    "id": "INTEGER", "production_line_id": "TEXT",
                    "sub_plan_version_id": "INTEGER", "version_no": "INTEGER",
                    "status": "TEXT", "definition_json": "TEXT",
                    "generation_source": "TEXT", "based_on_version_id": "INTEGER",
                    "created_at": "TEXT", "created_by": "TEXT",
                    "reviewed_at": "TEXT", "reviewed_by": "TEXT", "change_note": "TEXT",
                },
                "production_tasks": {
                    "id": "TEXT", "idempotency_key": "TEXT", "round_id": "INTEGER",
                    "master_plan_version_id": "INTEGER", "sub_plan_id": "TEXT",
                    "sub_plan_version_id": "INTEGER", "strategy_step_id": "TEXT",
                    "production_line_version_id": "INTEGER",
                    "platform": "TEXT", "task_ordinal": "INTEGER", "status": "TEXT",
                    "attempt_count": "INTEGER", "max_attempts": "INTEGER",
                    "content_id": "TEXT", "input_snapshot_json": "TEXT",
                    "error_code": "TEXT", "error_message": "TEXT",
                    "available_at": "TEXT", "started_at": "TEXT",
                    "finished_at": "TEXT", "lease_expires_at": "TEXT",
                    "created_at": "TEXT", "updated_at": "TEXT",
                },
                "production_task_attempts": {
                    "id": "INTEGER", "task_id": "TEXT", "attempt_no": "INTEGER",
                    "status": "TEXT", "worker_id": "TEXT", "started_at": "TEXT",
                    "finished_at": "TEXT", "error_code": "TEXT",
                    "error_message": "TEXT", "output_content_id": "TEXT",
                },
                "strategy_step_condition_reviews": {
                    "id": "INTEGER", "stage_id": "INTEGER", "condition_type": "TEXT",
                    "decision": "TEXT", "reason": "TEXT", "evidence_json": "TEXT",
                    "reviewed_by": "TEXT", "created_at": "TEXT",
                },
            }
            missing_columns = []
            column_type_mismatches = []
            for table_name, required in expected_columns.items():
                if table_name not in existing:
                    continue
                table_info = {
                    row["name"]: str(row["type"] or "").upper()
                    for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                }
                for column_name, expected_type in required.items():
                    actual_type = table_info.get(column_name)
                    if actual_type is None:
                        missing_columns.append((table_name, column_name))
                    elif actual_type != expected_type:
                        column_type_mismatches.append(
                            (table_name, column_name, expected_type, actual_type)
                        )
            result["missing_columns"] = sorted(missing_columns)
            result["column_type_mismatches"] = sorted(column_type_mismatches)

            expected_not_null_columns = {
                "drafts": {"publish_status", "publish_time_source", "review_status"},
                "sub_plan_stages": {
                    "strategy_goal", "current_problem", "execution_strategy",
                    "prerequisites", "input_info", "expected_result",
                    "completion_criteria", "effect_evaluation", "entry_condition",
                    "entry_condition_met", "dependencies_json",
                },
                "content_review_events": {
                    "content_id", "event_type", "actor", "reason", "created_at",
                },
                "strategy_effect_reviews": {
                    "stage_id", "assessment", "recommendation", "evidence_note",
                    "confidence", "evidence_json", "created_by", "created_at",
                },
                "sub_plan_stage_outcomes": {
                    "sub_plan_id", "stage_key", "source_version_id", "stage_id",
                    "result_json", "evaluation", "recorded_at", "recorded_by",
                },
                "production_lines": {"sub_plan_id", "status", "created_at", "updated_at", "created_by"},
                "production_line_versions": {
                    "production_line_id", "sub_plan_version_id", "version_no",
                    "status", "definition_json", "generation_source", "created_at", "created_by",
                },
                "production_tasks": {
                    "id", "idempotency_key", "round_id", "master_plan_version_id",
                    "sub_plan_id", "sub_plan_version_id", "strategy_step_id",
                    "production_line_version_id", "platform", "task_ordinal", "status",
                    "attempt_count", "max_attempts", "input_snapshot_json", "created_at", "updated_at",
                },
                "production_task_attempts": {
                    "task_id", "attempt_no", "status", "started_at",
                },
                "strategy_step_condition_reviews": {
                    "stage_id", "condition_type", "decision", "reason",
                    "evidence_json", "reviewed_by", "created_at",
                },
            }
            nullable_mismatches = []
            for table_name, required_columns in expected_not_null_columns.items():
                if table_name not in existing:
                    continue
                table_info = {
                    row["name"]: row
                    for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                }
                for column_name in required_columns:
                    column = table_info.get(column_name)
                    if column is not None and not bool(column["notnull"]):
                        nullable_mismatches.append((table_name, column_name, "NOT NULL"))
            result["nullable_mismatches"] = sorted(nullable_mismatches)

            expected_defaults = {
                "drafts": {
                    "publish_status": "'not_published'",
                    "publish_time_source": "'confirmation_time'",
                    "review_status": "'pending'",
                },
                "sub_plan_stages": {
                    "strategy_goal": "''", "current_problem": "''",
                    "execution_strategy": "''", "prerequisites": "''",
                    "input_info": "''", "expected_result": "''",
                    "completion_criteria": "''", "effect_evaluation": "''",
                    "entry_condition": "''", "entry_condition_met": "1",
                    "dependencies_json": "'[]'",
                    "business_completion_required": "0",
                    "business_completion_status": "'not_required'",
                    "effect_required": "0", "effect_status": "'not_required'",
                },
                "content_review_events": {"actor": "'web-ui'", "reason": "''"},
                "strategy_effect_reviews": {
                    "evidence_note": "''", "confidence": "'low'",
                    "evidence_json": "'{}'", "created_by": "'web-ui'",
                },
                "sub_plan_stage_outcomes": {
                    "evaluation": "''", "recorded_by": "'user'",
                },
                "production_lines": {"status": "'draft'", "created_by": "'system'"},
                "production_line_versions": {
                    "status": "'draft'", "generation_source": "'manual'", "created_by": "'user'",
                },
                "production_tasks": {
                    "task_ordinal": "1", "status": "'pending'",
                    "attempt_count": "0", "max_attempts": "3",
                    "input_snapshot_json": "'{}'",
                },
            }
            default_mismatches = []
            for table_name, defaults in expected_defaults.items():
                if table_name not in existing:
                    continue
                table_info = {
                    row["name"]: row
                    for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                }
                for column_name, expected_default in defaults.items():
                    column = table_info.get(column_name)
                    if column is not None and column["dflt_value"] != expected_default:
                        default_mismatches.append(
                            (table_name, column_name, expected_default, column["dflt_value"])
                        )
            result["default_mismatches"] = sorted(default_mismatches)

            expected_check_fragments = {
                "drafts": (
                    "publish_status IN ('not_published','published','failed')",
                    "publish_time_source IN ('confirmation_time','user_supplied')",
                    "review_status IN ('pending','approved','rejected')",
                ),
                "sub_plan_stages": (
                    "entry_condition_met IN (0,1)",
                    "business_completion_required IN (0,1)",
                    "business_completion_status IN ('not_required','pending','passed','failed','needs_review')",
                    "effect_required IN (0,1)",
                    "effect_status IN ('not_required','pending','passed','failed','needs_review')",
                ),
                "production_tasks": (
                    "status IN ('pending','running','succeeded','failed','cancelled')",
                    "task_ordinal > 0", "attempt_count >= 0", "max_attempts > 0",
                    "status != 'succeeded' OR content_id IS NOT NULL",
                ),
                "production_task_attempts": (
                    "attempt_no > 0",
                    "status IN ('running','succeeded','failed','cancelled','abandoned')",
                ),
                "strategy_step_condition_reviews": (
                    "condition_type IN ('business','effect')",
                    "decision IN ('passed','failed','needs_review')",
                ),
                "production_lines": (
                    "status IN ('draft','active','paused','completed','cancelled','archived')",
                ),
                "production_line_versions": (
                    "version_no > 0",
                    "status IN ('draft','effective','rejected','archived')",
                    "generation_source IN ('ai','manual','system')",
                ),
                "content_review_events": (
                    "event_type IN ('edited','approved','rejected','resubmitted')",
                ),
                "strategy_effect_reviews": (
                    "assessment IN ('effective','mixed','ineffective','insufficient_data')",
                    "length(trim(recommendation)) BETWEEN 1 AND 2000",
                    "confidence IN ('low','medium','high')",
                ),
            }
            missing_check_constraints = []
            for table_name, fragments in expected_check_fragments.items():
                if table_name not in existing:
                    continue
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                ddl = " ".join((row["sql"] or "").lower().split()) if row else ""
                for fragment in fragments:
                    if " ".join(fragment.lower().split()) not in ddl:
                        missing_check_constraints.append((table_name, fragment))
            result["missing_check_constraints"] = sorted(missing_check_constraints)

            # SQLite 默认不会在每次读取时自动暴露历史孤儿记录；
            # 显式检查可发现旧迁移或外部写入留下的外键损坏。
            result["foreign_key_violations"] = [
                tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()
            ]

            # Application-level checks are not enough for historical rows or direct SQL.
            # Surface cross-table provenance damage even when no physical FK exists.
            relationship_violations = {}
            required_relationship_tables = {
                "production_tasks", "production_lines", "production_line_versions",
                "spiral_rounds", "sub_plans", "sub_plan_versions",
                "sub_plan_stages", "contents",
            }
            if required_relationship_tables.issubset(existing):
                checks = {
                    "production_task_round_provenance": """
                        SELECT COUNT(*) FROM production_tasks t
                        LEFT JOIN spiral_rounds r ON r.id=t.round_id
                        LEFT JOIN sub_plans p ON p.id=t.sub_plan_id
                        LEFT JOIN sub_plan_stages s
                          ON s.id=r.sub_plan_stage_id
                         AND s.sub_plan_version_id=r.sub_plan_version_id
                        WHERE r.id IS NULL OR p.id IS NULL
                           OR r.master_plan_version_id!=t.master_plan_version_id
                           OR r.sub_plan_id!=t.sub_plan_id
                           OR r.sub_plan_version_id!=t.sub_plan_version_id
                           OR s.stage_key IS NOT t.strategy_step_id
                           OR p.platform!=t.platform
                           OR r.production_line_version_id IS NOT t.production_line_version_id
                    """,
                    "production_task_content_links": """
                        SELECT COUNT(*) FROM production_tasks t
                        LEFT JOIN contents c ON c.id=t.content_id
                        WHERE t.content_id IS NOT NULL
                          AND (c.id IS NULL OR c.production_task_id IS NOT t.id
                               OR c.round_id!=t.round_id OR c.platform!=t.platform)
                    """,
                    "content_task_links": """
                        SELECT COUNT(*) FROM contents c
                        LEFT JOIN production_tasks t ON t.id=c.production_task_id
                        WHERE c.production_task_id IS NOT NULL
                          AND (t.id IS NULL OR t.content_id IS NOT c.id
                               OR t.round_id!=c.round_id OR t.platform!=c.platform)
                    """,
                    "production_task_line_versions": """
                        SELECT COUNT(*) FROM production_tasks t
                        LEFT JOIN production_line_versions v ON v.id=t.production_line_version_id
                        LEFT JOIN production_lines l ON l.id=v.production_line_id
                        LEFT JOIN sub_plan_versions sv ON sv.id=v.sub_plan_version_id
                        WHERE t.production_line_version_id IS NOT NULL
                          AND (v.id IS NULL OR l.id IS NULL OR sv.id IS NULL
                               OR l.sub_plan_id!=t.sub_plan_id
                               OR sv.sub_plan_id!=t.sub_plan_id
                               OR v.sub_plan_version_id!=t.sub_plan_version_id)
                    """,
                    "production_line_current_versions": """
                        SELECT COUNT(*) FROM production_lines l
                        LEFT JOIN production_line_versions v ON v.id=l.current_version_id
                        LEFT JOIN sub_plan_versions sv ON sv.id=v.sub_plan_version_id
                        WHERE l.current_version_id IS NOT NULL
                          AND (v.id IS NULL OR sv.id IS NULL
                               OR v.production_line_id!=l.id
                               OR sv.sub_plan_id!=l.sub_plan_id)
                    """,
                    "production_line_version_subplans": """
                        SELECT COUNT(*) FROM production_line_versions v
                        LEFT JOIN production_lines l ON l.id=v.production_line_id
                        LEFT JOIN sub_plan_versions sv ON sv.id=v.sub_plan_version_id
                        WHERE l.id IS NULL OR sv.id IS NULL OR l.sub_plan_id!=sv.sub_plan_id
                    """,
                    "production_line_version_base_identity": """
                        SELECT COUNT(*) FROM production_line_versions v
                        LEFT JOIN production_line_versions parent ON parent.id=v.based_on_version_id
                        WHERE v.based_on_version_id IS NOT NULL
                          AND (parent.id IS NULL OR parent.production_line_id!=v.production_line_id)
                    """,
                }
                for name, sql in checks.items():
                    relationship_violations[name] = int(conn.execute(sql).fetchone()[0])
            result["relationship_violations"] = relationship_violations
            integrity_mode = "integrity_check" if full_integrity else "quick_check"
            integrity = conn.execute(f"PRAGMA {integrity_mode}").fetchone()
            result["integrity_check"] = integrity[0] if integrity else "no result"
            result["integrity_check_mode"] = integrity_mode

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


class _RecoverableRLock:
    """Reentrant lock that can recover ownership when a worker thread dies."""

    def __init__(self):
        self._condition = _threading.Condition(_threading.Lock())
        self._owner = None
        self._depth = 0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        current = _threading.current_thread()
        deadline = None if timeout is None or timeout < 0 else time.monotonic() + timeout
        with self._condition:
            while True:
                if self._owner is not None and not self._owner.is_alive():
                    self._owner = None
                    self._depth = 0
                    self._condition.notify_all()
                if self._owner is None or self._owner is current:
                    self._owner = current
                    self._depth += 1
                    return True
                if not blocking:
                    return False
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)

    def release(self) -> None:
        with self._condition:
            if self._owner is not _threading.current_thread():
                raise RuntimeError("cannot release un-acquired SQLite connection lock")
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._condition.notify_all()


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
        object.__setattr__(self, "_lock", _RecoverableRLock())
        object.__setattr__(self, "_transaction_owner", None)
        object.__setattr__(self, "_context_owner", None)
        object.__setattr__(self, "_context_depth", 0)
        object.__setattr__(self, "_rollback_only", False)

    @property
    def raw(self) -> sqlite3.Connection:
        return self._conn

    def _acquire_lock(self, timeout: float = 10.0) -> None:
        """Bound lock waits so a leaked transaction cannot freeze API workers forever."""
        if not self._lock.acquire(timeout=timeout):
            raise TimeoutError(
                f"SQLite connection lock wait exceeded {timeout:.1f}s"
            )

    def _acquire_lock_and_recover(self, timeout: float = 10.0) -> None:
        self._acquire_lock(timeout=timeout)
        try:
            self._recover_abandoned_state()
        except BaseException:
            self._lock.release()
            raise

    def rollback_if_open(self, timeout: float = 2.0) -> bool:
        """Best-effort request-finalizer safety net for an abandoned SQLite transaction."""
        owner = _threading.current_thread()
        # Never let an unrelated request finalizer roll back another live worker's
        # transaction or wait behind it. Dead owners are reclaimed below.
        for active_owner in (self._context_owner, self._transaction_owner):
            if active_owner is not None and active_owner is not owner and active_owner.is_alive():
                return False
        self._acquire_lock(timeout=timeout)
        release_retained = False
        rolled_back = False
        try:
            self._recover_abandoned_state()
            if self._conn.in_transaction:
                self._conn.rollback()
                rolled_back = True
            if self._transaction_owner is owner:
                object.__setattr__(self, "_transaction_owner", None)
                release_retained = True
            return rolled_back
        finally:
            if release_retained:
                self._lock.release()
            self._lock.release()

    def _recover_abandoned_state(self) -> None:
        """Rollback transactions whose owning worker exited without cleanup."""
        context_owner = self._context_owner
        transaction_owner = self._transaction_owner
        abandoned = (
            context_owner is not None and not context_owner.is_alive()
        ) or (
            transaction_owner is not None and not transaction_owner.is_alive()
        )
        if not abandoned:
            return
        try:
            if self._conn.in_transaction:
                self._conn.rollback()
        except BaseException as exc:
            # Never let a connection with an un-recoverable abandoned transaction
            # continue serving statements that might later commit partial work.
            try:
                self._conn.close()
            except Exception:
                logger.exception("关闭无法回滚的 SQLite 连接失败")
            raise RuntimeError("abandoned SQLite transaction rollback failed; connection closed") from exc
        finally:
            object.__setattr__(self, "_transaction_owner", None)
            object.__setattr__(self, "_context_owner", None)
            object.__setattr__(self, "_context_depth", 0)
            object.__setattr__(self, "_rollback_only", False)

    def _run_locked(self, operation):
        self._acquire_lock_and_recover()
        owner = _threading.current_thread()
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
        owner = _threading.current_thread()
        if self._context_owner == owner and self._context_depth > 0:
            # 请求/业务事务中的 commit 延迟到最外层 with conn 正常退出。
            return
        self._acquire_lock_and_recover()
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
        owner = _threading.current_thread()
        if self._context_owner == owner and self._context_depth > 0:
            object.__setattr__(self, "_rollback_only", True)
            return
        self._acquire_lock_and_recover()
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
        self._acquire_lock_and_recover()
        owner = _threading.current_thread()
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
        self._acquire_lock_and_recover()
        owner = _threading.current_thread()
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
        self._acquire_lock_and_recover()
        owner = _threading.current_thread()
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
        owner = _threading.current_thread()
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
        if getattr(conn, "_context_owner", None) is _threading.current_thread():
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
