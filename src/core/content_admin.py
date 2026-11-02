"""
已生产内容 / 轮次删除。

规则（不丢数据闭环）：
- 已发布链路上的内容（user_published / data_fetching / published）不能删：它们有指标，
  删了会让复盘、目标进度、评分历史失真。需要隐藏请改用驳回/取消。
- draft_saved：平台创作者中心里还留着这份草稿，系统不会去平台删（R1 只存草稿、不做平台写删除），
  返回 platform_drafts_left 提醒用户自己去平台清理。
- pending_verify：平台保存结果尚未核实，禁止删除本地记录，必须先完成人工核实。
- 数据库记录在单个 BEGIN IMMEDIATE 事务内删除；只有事务提交成功后才清理本地卡片图片
  （只删项目 output 目录下的文件），提交失败时图片原样保留。
- 轮次只有在没有任何不可删内容（含待核实草稿）时才能整轮删除；进行中的轮次不能删。
  轮次及其内容、关联记录处于同一事务，任何一步失败整体回滚。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.core.db import connection_transaction

logger = logging.getLogger(__name__)

LOCKED_DRAFT_STATUSES = ("user_published", "data_fetching", "published")
BUSY_ROUND_STATUSES = ("producing", "collecting_metrics", "analyzing")


class DeleteBlocked(ValueError):
    pass


def _remove_files(cfg, raw) -> int:
    try:
        paths = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        logger.warning("卡片文件路径清单无效，跳过文件清理")
        return 0
    root = cfg.resolve_path(cfg.paths.output_dir).resolve()
    removed = 0
    for path in paths or []:
        try:
            target = Path(path).resolve()
            if root in target.parents and target.is_file():
                target.unlink()
                removed += 1
        except OSError:
            logger.warning("卡片文件删除失败：%s", path, exc_info=True)
    return removed


def _optional_execute(conn, sql: str, params: tuple | list) -> None:
    """兼容历史库缺少可选表 / 列；其它数据库错误照常抛出并回滚整个事务。"""
    import sqlite3

    try:
        conn.execute(sql, params)
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "no such table" in message or "no such column" in message:
            logger.debug("跳过历史库缺失的可选表/列：%s", exc)
            return
        raise


def _delete_contents_in_transaction(conn, ids: list[str]) -> tuple[dict, list]:
    """只修改数据库并收集待清理文件清单；调用方必须包在同一个写事务中。"""
    deleted, blocked, not_found = [], [], []
    platform_left = 0
    file_manifests: list = []
    for cid in ids:
        row = conn.execute("SELECT id, image_paths FROM contents WHERE id = ?", (cid,)).fetchone()
        if row is None:
            not_found.append(cid)
            continue
        drafts = conn.execute(
            "SELECT id, status, user_confirmed_at FROM drafts WHERE content_id = ?", (cid,)
        ).fetchall()
        pending_verification = conn.execute(
            "SELECT 1 FROM draft_save_verifications v JOIN drafts d ON d.id=v.draft_id "
            "WHERE d.content_id=? AND v.status='pending_verify' LIMIT 1",
            (cid,),
        ).fetchone()
        if pending_verification or any(
            d["status"] in LOCKED_DRAFT_STATUSES or d["user_confirmed_at"] is not None
            for d in drafts
        ):
            blocked.append({
                "id": cid,
                "reason": "该内容的草稿保存结果尚待核实，或已有发布历史，不能删除；请先完成核实/归档",
            })
            continue
        platform_left += sum(1 for d in drafts if d["status"] == "draft_saved")
        draft_ids = [d["id"] for d in drafts]
        if draft_ids:
            marks = ",".join("?" * len(draft_ids))
            # 通知/快照/指标都外键引用 drafts，必须先清理，否则删轮次会 FK 失败
            _optional_execute(conn, f"DELETE FROM notifications WHERE draft_id IN ({marks})", draft_ids)
            _optional_execute(conn, f"DELETE FROM metric_snapshots WHERE draft_id IN ({marks})", draft_ids)
            conn.execute(f"DELETE FROM metrics WHERE draft_id IN ({marks})", draft_ids)
        _optional_execute(conn, "DELETE FROM metric_snapshots WHERE content_id = ?", (cid,))
        # 快速体验样稿被采纳后会引用正式内容；删除正式内容只解除引用，样稿本身保留。
        _optional_execute(
            conn, "UPDATE sample_contents SET promoted_content_id = NULL WHERE promoted_content_id = ?",
            (cid,),
        )
        conn.execute("DELETE FROM metrics WHERE content_id = ?", (cid,))
        conn.execute("DELETE FROM drafts WHERE content_id = ?", (cid,))
        conn.execute("DELETE FROM contents WHERE id = ?", (cid,))
        file_manifests.append(row["image_paths"])
        deleted.append(cid)
    result = {
        "deleted": deleted, "blocked": blocked, "not_found": not_found,
        "platform_drafts_left": platform_left, "files_removed": 0,
    }
    return result, file_manifests


def delete_contents(cfg, conn, ids: list[str]) -> dict:
    ids = [i for i in dict.fromkeys(str(x).strip() for x in ids) if i]
    if not ids:
        raise ValueError("ids 不能为空")
    # 文件系统不在 SQLite 事务内：先提交数据库，再做幂等文件清理。
    with connection_transaction(conn, immediate=True):
        result, file_manifests = _delete_contents_in_transaction(conn, ids)
    result["files_removed"] = sum(_remove_files(cfg, raw) for raw in file_manifests)
    return result


def delete_round(cfg, conn, round_id: int) -> dict:
    with connection_transaction(conn, immediate=True):
        row = conn.execute(
            "SELECT id, round_number, status FROM spiral_rounds WHERE id = ?", (round_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"轮次 {round_id} 不存在")
        if row["status"] in BUSY_ROUND_STATUSES:
            raise DeleteBlocked(
                f"第 {row['round_number']} 轮正在 {row['status']}，不能删除；等它结束或先取消"
            )
        locked = conn.execute(
            "SELECT COUNT(*) FROM drafts d JOIN contents c ON d.content_id = c.id "
            f"WHERE c.round_id = ? AND (d.user_confirmed_at IS NOT NULL "
            f"OR d.status IN ({','.join('?' * len(LOCKED_DRAFT_STATUSES))}))",
            (round_id, *LOCKED_DRAFT_STATUSES),
        ).fetchone()[0]
        if locked:
            raise DeleteBlocked(
                f"第 {row['round_number']} 轮有 {locked} 篇已发布或存在发布历史的内容，不能整轮删除"
            )
        cids = [r[0] for r in conn.execute("SELECT id FROM contents WHERE round_id = ?", (round_id,))]
        result, file_manifests = _delete_contents_in_transaction(conn, cids)
        if result["blocked"]:
            # 含待核实草稿的轮次不能整轮删除；抛出异常使本事务整体回滚，不留下半删状态。
            blocked_ids = "、".join(item["id"] for item in result["blocked"])
            raise DeleteBlocked(
                f"第 {row['round_number']} 轮包含草稿保存待核实或有发布历史的内容（{blocked_ids}），不能整轮删除"
            )
        for table in ("ops_logs", "platform_insights", "user_override", "autogrow_runs", "notifications"):
            _optional_execute(conn, f"UPDATE {table} SET round_id = NULL WHERE round_id = ?", (round_id,))
        conn.execute("UPDATE signature_history SET round_id = NULL WHERE round_id = ?", (round_id,))
        _optional_execute(
            conn, "DELETE FROM task_runs WHERE task_type = 'round_pipeline' AND biz_id = ?",
            (str(round_id),),
        )
        conn.execute("DELETE FROM spiral_rounds WHERE id = ?", (round_id,))
    files_removed = sum(_remove_files(cfg, raw) for raw in file_manifests)
    return {"round_id": round_id, "round_number": row["round_number"],
            "contents_deleted": len(result["deleted"]),
            "platform_drafts_left": result["platform_drafts_left"], "files_removed": files_removed}
