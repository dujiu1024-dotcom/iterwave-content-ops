import multiprocessing
import sqlite3
import time

import pytest

from src.core import image_generation_budget as budget_module
from src.core.image_generation_budget import (
    ImageBudgetExceeded,
    _release_provider_lease,
    _try_acquire_provider_lease,
    article_budget,
    image_generation_run,
    page_budget,
    provider_request,
    remaining_budget_seconds,
)


@pytest.fixture(autouse=True)
def isolate_provider_lease_database(tmp_path, monkeypatch):
    monkeypatch.setattr(
        budget_module, "_database_path", lambda: str(tmp_path / "provider-leases.db")
    )


def _acquire_slot_in_child(db_path, result_queue):
    result_queue.put(_try_acquire_provider_lease(db_path, ttl_seconds=60))


def test_primary_and_fallback_provider_requests_share_article_and_page_budget():
    with image_generation_run(max_calls=30, timeout_seconds=300) as run:
        with article_budget(max_calls=27, page_max_calls=3) as article:
            with page_budget():
                with provider_request():
                    pass
                with provider_request():
                    pass
                with provider_request():
                    pass
                with pytest.raises(ImageBudgetExceeded, match="当前页"):
                    with provider_request():
                        pass
            assert article.calls == 3
            assert run.snapshot()["api_calls"] == 3


def test_article_budget_caps_calls_across_multiple_pages():
    with image_generation_run(max_calls=30, timeout_seconds=300):
        with article_budget(max_calls=3, page_max_calls=3) as article:
            for _ in range(3):
                with page_budget():
                    with provider_request():
                        pass
            with page_budget():
                with pytest.raises(ImageBudgetExceeded, match="单篇"):
                    with provider_request():
                        pass
            assert article.calls == 3


def test_round_budget_is_shared_across_nested_article_contexts():
    with image_generation_run(max_calls=2, timeout_seconds=300) as run:
        for _ in range(2):
            with article_budget(max_calls=27, page_max_calls=3):
                with page_budget():
                    with provider_request():
                        pass
        with article_budget(max_calls=27, page_max_calls=3):
            with page_budget():
                with pytest.raises(ImageBudgetExceeded, match="本轮"):
                    with provider_request():
                        pass
        assert run.snapshot()["api_calls"] == 2


def test_run_budget_enforces_configured_concurrency_limit():
    with image_generation_run(max_calls=30, timeout_seconds=300, max_concurrency=1):
        with provider_request():
            with pytest.raises(ImageBudgetExceeded, match="并发数"):
                with provider_request():
                    pass


def test_hard_timeout_starts_at_first_budgeted_operation():
    with image_generation_run(max_calls=30, timeout_seconds=0.01):
        assert remaining_budget_seconds() > 0
        time.sleep(0.02)
        with pytest.raises(ImageBudgetExceeded, match="硬超时"):
            with provider_request():
                pass


def test_sqlite_provider_leases_cap_slots_and_release(tmp_path):
    db_path = str(tmp_path / "leases.db")
    first = _try_acquire_provider_lease(db_path, ttl_seconds=60)
    second = _try_acquire_provider_lease(db_path, ttl_seconds=60)
    third = _try_acquire_provider_lease(db_path, ttl_seconds=60)
    assert first and second and first != second
    assert third is None

    _release_provider_lease(db_path, first)
    replacement = _try_acquire_provider_lease(db_path, ttl_seconds=60)
    assert replacement
    assert _try_acquire_provider_lease(db_path, ttl_seconds=60) is None


def test_sqlite_provider_leases_recover_expired_process_slots(tmp_path):
    db_path = str(tmp_path / "leases.db")
    lease = _try_acquire_provider_lease(db_path, ttl_seconds=1)
    assert lease
    time.sleep(1.05)
    assert _try_acquire_provider_lease(db_path, ttl_seconds=60)


def test_sqlite_provider_leases_enforce_two_slots_across_processes(tmp_path):
    db_path = str(tmp_path / "leases.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE image_generation_provider_slots (
            slot_id INTEGER PRIMARY KEY CHECK (slot_id IN (1, 2)),
            lease_id TEXT UNIQUE,
            expires_at REAL
        )"""
    )
    conn.executemany(
        "INSERT INTO image_generation_provider_slots (slot_id) VALUES (?)",
        ((1,), (2,)),
    )
    conn.commit()
    conn.close()

    ctx = multiprocessing.get_context("fork")
    result_queue = ctx.Queue()
    workers = [
        ctx.Process(target=_acquire_slot_in_child, args=(db_path, result_queue))
        for _ in range(6)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    leases = [result_queue.get(timeout=2) for _ in workers]
    acquired = [lease for lease in leases if lease]
    assert len(acquired) == 2
    assert len(set(acquired)) == 2
