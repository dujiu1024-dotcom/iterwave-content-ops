"""Async-safe task correlation context for operation and notification audit rows."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional
from datetime import datetime
from zoneinfo import ZoneInfo


def business_zone(cfg):
    """Resolve the configured business timezone for scheduler timestamps."""
    return ZoneInfo(cfg.business_timezone)


def business_timestamp(cfg) -> float:
    """Return the current Unix timestamp using the configured business timezone."""
    return datetime.now(business_zone(cfg)).timestamp()


_task_run_id: ContextVar[Optional[int]] = ContextVar("iterwave_task_run_id", default=None)
_round_id: ContextVar[Optional[int]] = ContextVar("iterwave_round_id", default=None)


def current_task_run_id() -> Optional[int]:
    return _task_run_id.get()


def current_round_id() -> Optional[int]:
    return _round_id.get()


@contextmanager
def bind_task_context(task_run_id: Optional[int], round_id: Optional[int] = None) -> Iterator[None]:
    task_token = _task_run_id.set(task_run_id)
    round_token = _round_id.set(round_id)
    try:
        yield
    finally:
        _round_id.reset(round_token)
        _task_run_id.reset(task_token)
