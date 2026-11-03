"""任务实时进度上报（供 Web UI 展示“现在到哪一步”）。

进度快照写入全局任务队列的 progress_json，页面轮询 run_status / 任务详情即可看到
当前步骤、已完成步骤与计数。任何写入异常都会被吞掉并记录日志：进度只是展示信息，
绝不能因为进度写失败而中断真实的生成/存草稿流程。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_Sink = tuple[Any, str]
_sink: ContextVar[_Sink | None] = ContextVar("iterwave_task_progress_sink", default=None)


@contextmanager
def bind_task_progress(queue: Any, task_id: str) -> Iterator[None]:
    """在任务执行期间绑定进度落点；worker 内部使用。"""
    token = _sink.set((queue, task_id))
    try:
        yield
    finally:
        _sink.reset(token)


def _write(progress: dict[str, Any]) -> None:
    sink = _sink.get()
    if sink is None:
        return
    queue, task_id = sink
    progress["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _cache(queue)[task_id] = dict(progress)
    try:
        queue.update_progress(task_id, progress)
    except Exception:  # noqa: BLE001 — 进度写失败绝不能影响业务执行
        logger.warning("任务进度写入失败 task_id=%s", task_id, exc_info=True)


def _cache(queue: Any) -> dict[str, dict[str, Any]]:
    cached = getattr(queue, "_progress_cache", None)
    if not isinstance(cached, dict):
        cached = {}
        queue._progress_cache = cached
    return cached


def start_progress(steps: list[dict[str, Any]], *, detail: str = "") -> None:
    """初始化步骤清单：steps = [{"id","label"}]，全部 pending。"""
    _write({
        "steps": [
            {"id": str(item.get("id")), "label": str(item.get("label") or item.get("id")),
             "status": "pending", "detail": ""}
            for item in steps
            if isinstance(item, dict) and item.get("id")
        ],
        "current_step": None,
        "current_label": "",
        "detail": detail,
        "percent": 0,
    })


def update_step(step_id: str, *, status: str, detail: str = "") -> None:
    """更新单个步骤状态（pending / running / done / failed / skipped）。"""
    sink = _sink.get()
    if sink is None:
        return
    progress = _snapshot(sink)
    steps = progress.setdefault("steps", [])
    for step in steps:
        if step.get("id") == step_id:
            step["status"] = status
            if detail:
                step["detail"] = detail
            break
    done = sum(1 for step in steps if step.get("status") in ("done", "skipped", "failed"))
    progress["percent"] = int(round(100 * done / len(steps))) if steps else 0
    if status == "running":
        progress["current_step"] = step_id
        progress["current_label"] = next(
            (step["label"] for step in steps if step.get("id") == step_id), step_id,
        )
        progress["detail"] = detail
    elif progress.get("current_step") == step_id:
        progress["current_step"] = None
        progress["current_label"] = ""
    _write(progress)


def update_detail(detail: str) -> None:
    """只更新当前步骤的细节文案（如“已生成 3/20 篇”），不改变步骤状态。"""
    sink = _sink.get()
    if sink is None:
        return
    progress = _snapshot(sink)
    progress["detail"] = detail
    _write(progress)


def fail_current(reason: str) -> None:
    """把当前进行中的步骤标为失败，避免界面一直显示“运行中”。"""
    sink = _sink.get()
    if sink is None:
        return
    progress = _snapshot(sink)
    steps = progress.setdefault("steps", [])
    current = progress.get("current_step")
    for step in steps:
        if step.get("id") == current:
            step["status"] = "failed"
            step["detail"] = reason[:160]
            break
    done = sum(1 for step in steps if step.get("status") in ("done", "skipped", "failed"))
    progress["percent"] = int(round(100 * done / len(steps))) if steps else 0
    progress["current_step"] = None
    progress["current_label"] = ""
    progress["detail"] = reason[:200]
    _write(progress)


def _snapshot(sink: _Sink) -> dict[str, Any]:
    """取最近一次进度快照（由 _write 缓存），避免逐次覆盖时丢失已完成步骤。"""
    queue, task_id = sink
    return dict(_cache(queue).get(task_id) or {})
