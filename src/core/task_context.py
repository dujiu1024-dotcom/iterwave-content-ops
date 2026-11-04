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
_config_context: ContextVar[object | None] = ContextVar("iterwave_config_context", default=None)


_initiating_actor: ContextVar[Optional[str]] = ContextVar("iterwave_initiating_actor", default=None)
_executing_actor: ContextVar[Optional[str]] = ContextVar("iterwave_executing_actor", default=None)
_tenant_id: ContextVar[Optional[str]] = ContextVar("iterwave_task_tenant_id", default=None)
_queue_task_id: ContextVar[Optional[str]] = ContextVar("iterwave_queue_task_id", default=None)


def current_task_identity() -> dict[str, Optional[str]]:
    """Return trusted identity metadata bound to the active background task."""
    return {
        "initiating_actor": _initiating_actor.get(),
        "executing_actor": _executing_actor.get(),
        "tenant_id": _tenant_id.get(),
        "task_id": _queue_task_id.get(),
    }


@contextmanager
def bind_task_identity(
    *, initiating_actor: Optional[str], executing_actor: str,
    tenant_id: str, task_id: str,
) -> Iterator[None]:
    """Bind immutable enqueue-time actor and worker identity for nested services.

    Missing legacy initiators remain explicitly unattributed; the worker never
    invents a human identity from a tenant or queue payload default.
    """
    tokens = (
        _initiating_actor.set(initiating_actor or "system:legacy-unattributed"),
        _executing_actor.set(executing_actor),
        _tenant_id.set(tenant_id),
        _queue_task_id.set(task_id),
    )
    try:
        yield
    finally:
        _queue_task_id.reset(tokens[3])
        _tenant_id.reset(tokens[2])
        _executing_actor.reset(tokens[1])
        _initiating_actor.reset(tokens[0])



def current_config():
    """Return the tenant config bound to the current background task, if any."""
    return _config_context.get()


@contextmanager
def bind_config(cfg) -> Iterator[None]:
    """Bind a tenant-specific config for nested helpers that lack an explicit cfg arg."""
    token = _config_context.set(cfg)
    try:
        yield
    finally:
        _config_context.reset(token)


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
