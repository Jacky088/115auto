"""网页端待确认暂存、撤回、忽略与提交队列原子状态机。"""
from __future__ import annotations

import asyncio
import json
import time

from ... import database as db
from ...core import transfer_gate
from .common import STATUS_ZH, is_system_running


def _mw():
    """延迟获取 media_worker 门面引用，避免子模块间循环导入并确保单测 patch 透传。"""
    from .. import media_worker
    return media_worker


def stage_task_selection(
    task_id: int,
    cand_idx: int | None = None,
    manual_cand: dict | None = None,
    source: str = "web",
) -> dict:
    """网页端原子『暂存候选』操作（只记录选定候选为 `staged`，绝不提交或动 115）。"""
    now = time.time()

    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone()
        if not row:
            return {"ok": False, "code": "not_found", "msg": "该任务已不存在"}
        r = dict(row)
        status = str(r.get("status") or "")

        if status not in ("need_confirm", "staged"):
            return {
                "ok": False,
                "code": "locked_status",
                "msg": f"该条已处于「{STATUS_ZH.get(status, status)}」状态，不可再更改",
                "task": r,
            }

        if manual_cand:
            chosen = manual_cand
        else:
            cands = json.loads(r.get("candidates_json") or "[]")
            if cand_idx is None or cand_idx < 0 or cand_idx >= len(cands):
                return {"ok": False, "code": "bad_index", "msg": "候选项序号无效", "task": r}
            chosen = cands[cand_idx]

        try:
            new_tmdb_id = int(chosen.get("tmdb_id") or 0)
        except (TypeError, ValueError):
            new_tmdb_id = 0
        if not new_tmdb_id:
            return {"ok": False, "code": "bad_candidate", "msg": "候选项缺少有效的 TMDB ID", "task": r}
        conn.execute(
            "UPDATE media_tasks SET status='staged', selected_tmdb_id=?, selected_kind=?, selected_title=?, "
            "selected_year=?, selected_poster=?, select_source=?, updated_at=? WHERE id=?",
            (
                new_tmdb_id,
                str(chosen.get("kind") or "movie"),
                str(chosen.get("title") or ""),
                str(chosen.get("year") or ""),
                str(chosen.get("poster_path") or ""),
                source,
                now,
                int(task_id),
            ),
        )
        updated = dict(conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone())

    return {"ok": True, "code": "staged", "msg": f"已暂存：{updated['selected_title']}（等待提交队列）", "task": updated}


def unstage_task_selection(task_id: int, source: str = "web") -> dict:
    """撤销暂存：从 `staged` 安全回退到 `need_confirm`。"""
    now = time.time()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone()
        if not row:
            return {"ok": False, "code": "not_found", "msg": "该任务已不存在"}
        r = dict(row)
        if r["status"] != "staged":
            return {"ok": False, "code": "locked_status", "msg": f"当前状态为「{STATUS_ZH.get(r['status'], r['status'])}」，无法撤销暂存", "task": r}
        conn.execute(
            "UPDATE media_tasks SET status='need_confirm', selected_tmdb_id=NULL, selected_kind='', "
            "selected_title='', selected_year='', selected_poster='', select_source='', updated_at=? WHERE id=?",
            (now, int(task_id)),
        )
        updated = dict(conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone())

    return {"ok": True, "code": "need_confirm", "msg": "已撤销暂存，恢复待确认", "task": updated}


def ignore_task(task_id: int, source: str = "web") -> dict:
    """将待确认或已暂存条目置为 `ignored`。"""
    now = time.time()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone()
        if not row:
            return {"ok": False, "code": "not_found", "msg": "该任务已不存在"}
        r = dict(row)
        if r["status"] not in ("need_confirm", "staged"):
            return {"ok": False, "code": "locked_status", "msg": f"该条已处于「{STATUS_ZH.get(r['status'], r['status'])}」状态", "task": r}
        conn.execute(
            "UPDATE media_tasks SET status='ignored', select_source=?, updated_at=?, finished_at=? WHERE id=?",
            (source, now, now, int(task_id)),
        )
        updated = dict(conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone())

    return {"ok": True, "code": "ignored", "msg": "已忽略此条目", "task": updated}


def commit_staged_tasks(
    task_ids: list[int] | None = None,
    tg_batch_id: str = "",
    source: str = "web",
) -> dict:
    """统一『🚀 提交队列』按钮逻辑：受顶部 running 开关与四方互斥保护，将 `staged -> queued` 并唤醒串行刮削执行。"""
    if not is_system_running():
        return {"ok": False, "code": "system_stopped", "msg": "系统顶部运行开关处于「已停止」，禁止提交执行！已为您保留已选条目。"}

    active_mode = transfer_gate.get_active_mode()
    if active_mode in ("organize", "auto_move"):
        mode_zh = "目录整理" if active_mode == "organize" else "定时文件整理"
        return {"ok": False, "code": "mutex_busy", "msg": f"当前正在执行【{mode_zh}】，互斥保护下无法同时启动媒体刮削，请待结束后再提交"}

    now = time.time()
    committed_rows: list[dict] = []

    with db.transaction() as conn:
        sql = "SELECT * FROM media_tasks WHERE status='staged' AND task_kind='scrape'"
        params: list = []
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            sql += f" AND id IN ({placeholders})"
            params.extend(int(x) for x in task_ids)
        elif tg_batch_id:
            sql += " AND tg_batch_id=?"
            params.append(str(tg_batch_id))
        sql += " ORDER BY id ASC"
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        if not rows:
            return {"ok": False, "code": "empty", "msg": "暂无「已选待提交」条目，请先点选至少一部影视候选"}

        ids_to_commit = [int(r["id"]) for r in rows]
        ph = ",".join("?" for _ in ids_to_commit)
        conn.execute(
            f"UPDATE media_tasks SET status='queued', updated_at=? WHERE id IN ({ph}) AND status='staged'",
            tuple([now] + ids_to_commit),
        )
        committed_rows = [dict(r) for r in conn.execute(f"SELECT * FROM media_tasks WHERE id IN ({ph})", tuple(ids_to_commit)).fetchall()]

    return {
        "ok": True,
        "code": "committed",
        "committed_count": len(committed_rows),
        "committed_ids": [int(r["id"]) for r in committed_rows],
        "msg": f"已提交 {len(committed_rows)} 条进入顺序执行队列（受防风控队列串行调度）",
    }


def schedule_commit_runner_if_idle() -> bool:
    """提交队列后，若当前刮削循环未运行且门禁空闲，则在当前事件循环中拉起 `commit_only` 消费循环。"""
    t = _mw()._ACTIVE_TASK_REF.get("scrape")
    if t and not t.done():
        _mw()._COMMIT_WAKEUP_REQUESTED = True
        return False
    if transfer_gate.is_exclusive_busy():
        if transfer_gate.get_active_mode() == "scrape":
            _mw()._COMMIT_WAKEUP_REQUESTED = True
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _mw()._COMMIT_WAKEUP_REQUESTED = False
    from .scrape import execute_scrape_queue
    _mw()._ACTIVE_TASK_REF["scrape"] = loop.create_task(execute_scrape_queue(mode="commit_only"))
    return True
