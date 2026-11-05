"""Usage tracking and concurrency coordination for image-provider requests.

Provider fallback calls consume the same counters as primary calls. Context variables
propagate through asyncio.to_thread. A SQLite lease table enforces provider concurrency
across threads and processes sharing the application database.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
import sqlite3
import threading
import time
import uuid

logger = logging.getLogger(__name__)

_GLOBAL_PROVIDER_CONCURRENCY = 2
# Expired leases recover abandoned provider slots; this is not a request timeout.
_PROVIDER_LEASE_TTL_SECONDS = 600


class ImageBudgetExceeded(RuntimeError):
    """Image-provider concurrency coordination could not be satisfied."""


def _database_path() -> str:
    """Resolve the shared SQLite path lazily to avoid import cycles at module load."""
    from .config import get_config
    cfg = get_config()
    return str(cfg.resolve_path(cfg.paths.db_file))


def _try_acquire_provider_lease(db_path: str, *, ttl_seconds: int = _PROVIDER_LEASE_TTL_SECONDS) -> str | None:
    """Atomically claim one of two global provider slots; return its lease id."""
    lease_id = uuid.uuid4().hex
    now = time.time()
    conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS image_generation_provider_slots (
                slot_id INTEGER PRIMARY KEY CHECK (slot_id IN (1, 2)),
                lease_id TEXT UNIQUE,
                expires_at REAL
            )"""
        )
        conn.executemany(
            "INSERT OR IGNORE INTO image_generation_provider_slots (slot_id) VALUES (?)",
            ((1,), (2,)),
        )
        conn.execute(
            """UPDATE image_generation_provider_slots
               SET lease_id = NULL, expires_at = NULL
               WHERE lease_id IS NOT NULL AND expires_at <= ?""",
            (now,),
        )
        row = conn.execute(
            """SELECT slot_id FROM image_generation_provider_slots
               WHERE lease_id IS NULL ORDER BY slot_id LIMIT 1"""
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        conn.execute(
            """UPDATE image_generation_provider_slots
               SET lease_id = ?, expires_at = ?
               WHERE slot_id = ? AND lease_id IS NULL""",
            (lease_id, now + max(1, int(ttl_seconds)), int(row[0])),
        )
        # BEGIN IMMEDIATE serializes contenders, so the selected row remains ours.
        conn.commit()
        return lease_id
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _release_provider_lease(db_path: str, lease_id: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute(
            """UPDATE image_generation_provider_slots
               SET lease_id = NULL, expires_at = NULL
               WHERE lease_id = ?""",
            (lease_id,),
        )
        conn.commit()
    finally:
        conn.close()


@dataclass
class ImageBudget:
    started_at: float | None = None
    calls: int = 0
    failures: int = 0
    max_concurrency: int = 2
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _semaphore: threading.BoundedSemaphore = field(
        default_factory=lambda: threading.BoundedSemaphore(2), repr=False,
    )

    def remaining_seconds(self) -> None:
        """No run-wide deadline is imposed; provider clients use their own transport policy."""
        if self.started_at is None:
            self.started_at = time.monotonic()
        return None

    def reserve(self) -> None:
        with self._lock:
            if self.started_at is None:
                self.started_at = time.monotonic()
            self.calls += 1

    def failed(self) -> None:
        with self._lock:
            self.failures += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "api_calls": self.calls,
                "failed_calls": self.failures,
                "elapsed_seconds": round(time.monotonic() - self.started_at, 3) if self.started_at is not None else 0.0,
            }


@dataclass
class ArticleBudget:
    calls: int = 0
    page_calls: int = 0
    failures: int = 0


_CURRENT_RUN: ContextVar[ImageBudget | None] = ContextVar("image_budget_run", default=None)
_CURRENT_ARTICLE: ContextVar[ArticleBudget | None] = ContextVar("image_budget_article", default=None)


def has_active_run() -> bool:
    return _CURRENT_RUN.get() is not None


def has_active_article() -> bool:
    return _CURRENT_ARTICLE.get() is not None


def remaining_budget_seconds() -> float | None:
    """Compatibility shim: image generation has no run-wide hard deadline."""
    return None


@contextmanager
def image_generation_run(*, max_calls: int | None = None,
                         timeout_seconds: int | None = None,
                         max_concurrency: int = 2):
    existing = _CURRENT_RUN.get()
    if existing is not None:
        yield existing
        return
    budget = ImageBudget(
        max_concurrency=max_concurrency,
        _semaphore=threading.BoundedSemaphore(max_concurrency),
    )
    token = _CURRENT_RUN.set(budget)
    try:
        yield budget
    finally:
        logger.info("image generation budget summary %s", budget.snapshot())
        _CURRENT_RUN.reset(token)


@contextmanager
def article_budget(*, max_calls: int | None = None,
                   page_max_calls: int | None = None):
    # Legacy parameters are accepted for compatibility but never enforce quotas.
    article = ArticleBudget()
    token = _CURRENT_ARTICLE.set(article)
    try:
        yield article
    finally:
        _CURRENT_ARTICLE.reset(token)
        logger.info(
            "image article usage api_calls=%s failed_calls=%s",
            article.calls, article.failures,
        )


@contextmanager
def page_budget():
    article = _CURRENT_ARTICLE.get()
    if article is None:
        yield
        return
    article.page_calls = 0
    try:
        yield article
    finally:
        article.page_calls = 0


def reserve_provider_call() -> float | None:
    """Record provider usage without imposing per-run, per-article, or per-page quotas."""
    run = _CURRENT_RUN.get()
    article = _CURRENT_ARTICLE.get()
    if run is not None:
        run.reserve()
    if article is not None:
        article.calls += 1
        article.page_calls += 1
    return None


@contextmanager
def provider_request():
    run = _CURRENT_RUN.get()
    with provider_slot():
        remaining = reserve_provider_call()
        try:
            yield remaining
        except Exception:
            if run is not None:
                run.failed()
            article = _CURRENT_ARTICLE.get()
            if article is not None:
                article.failures += 1
            raise


@contextmanager
def provider_slot():
    """Enforce concurrency safety only; usage is counted but never quota-blocked."""
    run = _CURRENT_RUN.get()
    acquired_local = False
    lease_id = None
    db_path = None
    if run is not None:
        acquired_local = run._semaphore.acquire(blocking=False)
        if not acquired_local:
            raise ImageBudgetExceeded(
                f"当前图片生成并发数已达到安全上限（{run.max_concurrency}）"
            )
    try:
        try:
            db_path = _database_path()
            lease_id = _try_acquire_provider_lease(db_path)
        except Exception as exc:
            raise ImageBudgetExceeded("无法确认系统图片生成并发状态，已阻止图片 API 请求") from exc
        if lease_id is None:
            raise ImageBudgetExceeded(
                f"系统图片生成并发数已达到安全上限（{_GLOBAL_PROVIDER_CONCURRENCY}），请稍后重试"
            )
        # Keep request exceptions outside the acquisition error handler. They must
        # reach generate_image_bytes so the configured fallback provider can run.
        yield
    finally:
        if lease_id is not None and db_path is not None:
            try:
                _release_provider_lease(db_path, lease_id)
            except Exception:
                logger.exception("failed to release image provider concurrency lease")
        if acquired_local and run is not None:
            run._semaphore.release()
