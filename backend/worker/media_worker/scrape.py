"""TMDB 媒体刮削执行引擎：自动识别、暂存/提交/忽略原子事务与 Web↔TG 实时联动。"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

from ... import database as db
from ...core import logstream, transfer_gate
from ...core.media_parser import ParsedMedia, format_media_target
from ...core.tmdb_scraper import (
    compact_candidates,
    compute_local_meta_dir,
    decide_match,
    remap_absolute_episode,
    save_local_metadata,
)
from ...pan115.client import normalize_title
from .. import jobs
from .common import (
    MediaCancelled,
    _RiskCircuitBreak,
    _check_not_cancelled,
    _get_lock,
    _interruptible_batch_rest,
    _is_risk_error,
    _pacing_sleep,
    get_media_settings,
    is_system_running,
)
from .reports import _build_scrape_tg_report

def _mw():
    """延迟获取 media_worker 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import media_worker
    return media_worker


STATUS_ZH = {
    "queued": "排队执行中",
    "running": "正在处理",
    "need_confirm": "待确认",
    "staged": "已选待提交",
    "done": "已完成",
    "skipped": "已跳过",
    "failed": "执行失败",
    "ignored": "已忽略",
}


async def _finalize_scraped_task(
    task_row: dict,
    cand_detail: dict,
    kind: str,
    settings: dict,
    batch_id: str,
    api_key: str,
    caches: tuple[dict, dict, dict, list],
) -> dict:
    """将已确认 TMDB 身份的单个（或按剧聚合的多集）任务执行 115 规范落盘 + 本地写 `.nfo` / 下载海报（零 115 上传）。"""
    dest_root_cache, subdir_cache, dir_entries_cache, moved_parent_cids = caches
    parsed_dict = json.loads(task_row.get("parsed_json") or "{}")
    pm = ParsedMedia(**{k: v for k, v in parsed_dict.items() if k in ParsedMedia.__dataclass_fields__})

    tmdb_id = int(cand_detail["id"])
    title = str(cand_detail.get("name") or cand_detail.get("title") or task_row.get("selected_title") or pm.title or "").strip()
    date_str = str(cand_detail.get("first_air_date") or cand_detail.get("release_date") or "").strip()
    year = date_str[:4] if len(date_str) >= 4 else str(task_row.get("selected_year") or pm.year or "")

    # 回查 rule 取 dest_root 与配对字幕信息
    rule = await asyncio.to_thread(db.query, "SELECT * FROM media_rules WHERE id=?", (task_row.get("rule_id") or 0,), one=True)
    dest_root = "/" + str((rule["target_dir"] if rule and rule["target_dir"] else task_row["source_dir"])).strip("/")

    tmdb_meta = {"tmdb_id": tmdb_id, "title": title, "year": year, "media_type": kind}

    grouped_snaps = parsed_dict.get("_grouped_items") or []
    if grouped_snaps:
        work_items = grouped_snaps
    else:
        fallback_snap = parsed_dict.get("_item_snapshot") or {
            "file_id": task_row["file_id"],
            "parent_cid": parsed_dict.get("_parent_cid") or task_row["source_cid"],
            "source_cid": task_row["source_cid"],
            "source_dir": task_row["source_dir"],
            "dest_root": dest_root,
            "orig_name": task_row["orig_name"],
            "orig_path": task_row["orig_path"],
            "size": int(parsed_dict.get("_size") or 0),
            "sha1": str(parsed_dict.get("_sha1") or ""),
            "is_bdmv": bool(pm.is_bdmv),
            "subtitles": parsed_dict.get("_subtitles") or [],
            "parsed": parsed_dict,
        }
        work_items = [fallback_snap]

    season_nums: set[int] = set()
    ep_items: list[tuple[int, int, str]] = []
    first_show_folder = ""
    last_placement: dict = {"status": "done", "dest_path": "", "dest_name": "", "reason": ""}
    first_ok_placement: dict | None = None
    placed_paths: list[str] = []
    subtitles_moved = 0
    ok_cnt = 0
    ep_fail_cnt = 0
    consec_ep_risk = 0
    ep_write_ops = 0
    in_place_cnt = 0
    last_err = ""

    for g_idx, g_snap in enumerate(work_items):
        _check_not_cancelled()
        g_pdict = g_snap.get("parsed") if isinstance(g_snap.get("parsed"), dict) else parsed_dict
        g_pm = ParsedMedia(**{k: v for k, v in g_pdict.items() if k in ParsedMedia.__dataclass_fields__})
        if kind == "tv" and g_pm.episode is not None:
            mapped_s, mapped_e, mapped_end = remap_absolute_episode(
                g_pm.season, g_pm.episode, g_pm.episode_end, cand_detail
            )
            base_s = 1 if g_pm.season is None else g_pm.season
            if (mapped_s, mapped_e, mapped_end) != (base_s, g_pm.episode, g_pm.episode_end):
                g_pm = ParsedMedia(**{**g_pm.__dict__, "season": mapped_s, "episode": mapped_e, "episode_end": mapped_end})

        rel_dir, target_name = format_media_target(g_pm, settings, tmdb_meta=tmdb_meta)
        if not first_show_folder:
            first_show_folder = rel_dir.split("/")[0] if "/" in rel_dir else rel_dir

        item_payload = dict(g_snap)
        item_payload.setdefault("source_cid", task_row["source_cid"])
        item_payload.setdefault("source_dir", task_row["source_dir"])
        item_payload.setdefault("parent_cid", parsed_dict.get("_parent_cid") or task_row["source_cid"])
        item_payload.setdefault("size", int(parsed_dict.get("_size") or 0))
        item_payload.setdefault("sha1", str(parsed_dict.get("_sha1") or ""))
        item_payload.setdefault("is_bdmv", bool(g_pm.is_bdmv))
        item_payload.setdefault("subtitles", [])
        item_payload["parsed"] = g_pm
        item_payload["dest_root"] = dest_root

        try:
            placement = await _mw().client.run_pan_thread(
                _mw()._apply_media_placement_sync,
                item_payload, rel_dir, target_name, settings, batch_id, int(task_row["id"]), tmdb_id,
                dest_root_cache, subdir_cache, dir_entries_cache, moved_parent_cids,
            )
        except Exception as ge:
            if len(work_items) == 1:
                raise
            ep_fail_cnt += 1
            last_err = str(ge)
            logstream.warn("scrape", f"剧集分组落盘失败 {g_snap.get('orig_name', '')}: {ge}")
            if _is_risk_error(ge):
                consec_ep_risk += 1
                if consec_ep_risk >= 2:
                    logstream.error("scrape", "🚨 刮削批量落盘连续触发 2 次 115 风控拦截，已立即安全熔断并恢复主程序队列！")
                    raise _RiskCircuitBreak("连续触发 115 风控限制，刮削落盘已安全熔断")
            else:
                consec_ep_risk = 0
            continue

        consec_ep_risk = 0
        last_placement = placement
        if first_ok_placement is None:
            first_ok_placement = placement
        if placement.get("dest_path"):
            dp = str(placement["dest_path"]).rstrip("/")
            if dp and dp not in placed_paths:
                placed_paths.append(dp)
        subtitles_moved += int(placement.get("subtitles_moved") or 0)
        ok_cnt += 1
        if placement.get("status") == "done":
            ep_write_ops += 1
        elif placement.get("in_place"):
            in_place_cnt += 1

        if kind == "tv":
            season_num = 0 if g_pm.is_special else (g_pm.season if g_pm.season is not None else 1)
            season_nums.add(season_num)
            if g_pm.episode is not None:
                ep_stem = os.path.splitext(placement.get("dest_name") or target_name)[0]
                ep_items.append((season_num, g_pm.episode, ep_stem))

        if g_idx + 1 < len(work_items) and placement.get("status") == "done":
            await _pacing_sleep(settings)
            await _interruptible_batch_rest(settings, ep_write_ops, "scrape")

    if len(work_items) > 1 and ok_cnt == 0 and ep_fail_cnt > 0:
        raise RuntimeError(last_err or "同剧分组落盘全部失败")

    rep_place = first_ok_placement or last_placement
    conflict_skipped_all = (ep_write_ops == 0 and in_place_cnt == 0 and rep_place.get("status") == "skipped")
    # 本地生成 .nfo 与下载海报（零 115 上传请求；仅对已移动或已在规范位置的影片落盘元数据）
    local_dir_str = ""
    has_nfo = False
    has_poster = False
    if not conflict_skipped_all and (settings.get("media_scrape_write_local_nfo", True) or settings.get("media_scrape_download_poster", True)):
        if not first_show_folder:
            rel0, _ = format_media_target(pm, settings, tmdb_meta=tmdb_meta)
            first_show_folder = rel0.split("/")[0] if "/" in rel0 else rel0
        meta_dir = compute_local_meta_dir(first_show_folder, str(settings.get("media_local_meta_dir") or ""))
        meta_res = await asyncio.to_thread(
            save_local_metadata,
            meta_dir, cand_detail, kind,
            season_nums=sorted(season_nums) if kind == "tv" and season_nums else ([1] if kind == "tv" else None),
            episode_items=ep_items,
            api_key=api_key,
            write_nfo=bool(settings.get("media_scrape_write_local_nfo", True)),
            download_poster=bool(settings.get("media_scrape_download_poster", True)),
        )
        local_dir_str = meta_res.get("meta_dir", "")
        saved_meta_files = list(meta_res.get("files") or [])
        has_nfo = any(str(p).lower().endswith(".nfo") for p in saved_meta_files)
        has_poster = bool(meta_res.get("poster_local")) or any(
            str(p).lower().endswith((".jpg", ".jpeg", ".png")) for p in saved_meta_files
        )

    now = time.time()
    final_status = "skipped" if conflict_skipped_all else (
        "done" if rep_place["status"] in ("done", "skipped") else rep_place["status"]
    )
    dest_name_disp = rep_place["dest_name"]
    reason_disp = rep_place.get("reason", "")
    if len(work_items) > 1:
        dest_name_disp = f"{rep_place['dest_name']} 等{ok_cnt}集"
        if ep_fail_cnt > 0:
            reason_disp = f"共 {len(work_items)} 集：成功 {ok_cnt}，失败 {ep_fail_cnt}"
        elif not reason_disp:
            reason_disp = f"同剧合并落盘共 {ok_cnt} 集"

    await asyncio.to_thread(
        db.execute,
        "UPDATE media_tasks SET status=?, media_type=?, target_path=?, target_name=?, selected_tmdb_id=?, "
        "selected_kind=?, selected_title=?, selected_year=?, local_meta_dir=?, error=?, updated_at=?, finished_at=? WHERE id=?",
        (
            final_status, kind, rep_place["dest_path"], dest_name_disp, tmdb_id, kind,
            title, year, local_dir_str, reason_disp, now, now, task_row["id"],
        ),
    )
    return {
        "status": final_status,
        "dest_path": rep_place["dest_path"],
        "dest_paths": placed_paths,
        "dest_name": dest_name_disp,
        "local_meta_dir": local_dir_str,
        "title": title,
        "year": year,
        "tmdb_id": tmdb_id,
        "kind": kind,
        "ok_count": max(1, ok_cnt),
        "subtitles_moved": subtitles_moved,
        "has_nfo": has_nfo,
        "has_poster": has_poster,
        "is_bdmv": bool(pm.is_bdmv),
    }


# ---------- 网页端待确认暂存、撤回、忽略与提交队列 ----------


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
    _mw()._ACTIVE_TASK_REF["scrape"] = loop.create_task(execute_scrape_queue(mode="commit_only"))
    return True


def _placed_to_report_item(placed: dict, fallback_orig: str) -> dict:
    """将 `_finalize_scraped_task` 返回结构转换为 `_format_media_detail_lines` 条目。"""
    return {
        "media_type": str(placed.get("kind") or "movie"),
        "title": str(placed.get("title") or fallback_orig or "未命名媒体"),
        "year": str(placed.get("year") or ""),
        "tmdb_id": placed.get("tmdb_id"),
        "count": max(1, int(placed.get("ok_count") or 1)),
        "sub_count": int(placed.get("subtitles_moved") or 0),
        "is_bdmv": bool(placed.get("is_bdmv")),
        "has_nfo": bool(placed.get("has_nfo")),
        "has_poster": bool(placed.get("has_poster")),
        "dest_path": str(placed.get("dest_path") or ""),
        "dest_paths": list(placed.get("dest_paths") or []),
        "dest_name": str(placed.get("dest_name") or ""),
    }


# ---------- 模块 B：媒体刮削严格单队列引擎 ----------

async def execute_scrape_queue(mode: str = "scan_and_run", rule_ids: list[int] | None = None) -> dict:
    """模块 B：媒体刮削引擎（`scan_and_run` 扫描+刮削，或 `commit_only` 消费人工已确认队列）。"""
    if not is_system_running():
        return {"ok": False, "msg": "系统处于停止状态，请先点击顶栏「运行」启动系统"}

    settings = await asyncio.to_thread(get_media_settings)
    api_key = str(settings.get("tmdb_api_key") or "").strip()
    if not api_key:
        return {"ok": False, "msg": "请先在「系统设置」填写有效的 TMDB API Key 再运行媒体刮削"}

    try:
        conf_th = float(settings.get("media_scrape_confidence_threshold", 85) or 85)
    except (TypeError, ValueError):
        conf_th = 85.0
    try:
        margin_th = float(settings.get("media_scrape_margin_threshold", 12) or 12)
    except (TypeError, ValueError):
        margin_th = 12.0

    lock = _get_lock("scrape")
    if lock.locked():
        return {"ok": False, "msg": "媒体刮削任务正在执行中，请勿重复发起"}

    ok, busy_msg = transfer_gate.try_begin_exclusive("scrape")
    if not ok:
        return {"ok": False, "msg": busy_msg}

    started_at = time.monotonic()
    run_batch_id = f"scr_{uuid.uuid4().hex[:10]}"
    auto_cnt = 0
    confirm_cnt = 0
    fail_cnt = 0
    skip_cnt = 0
    rule_cnt = 0
    total = 0
    removed_dirs = 0
    auto_items: list[dict] = []
    pending_map: dict[int, dict] = {}
    fail_items: list[tuple[str, str]] = []
    outcome = "done"
    outcome_msg = ""
    result_payload: dict = {}
    rerun_committed = False

    try:
        async with lock:
            transfer_gate.clear_cancel()
            transfer_gate.set_media_progress({"mode": "scrape", "phase": "draining", "current": 0, "total": 0, "label": "等待转存队列消化"})
            drained = await transfer_gate.prepare_and_drain_transfers(mode="scrape")
            if drained is False:
                raise MediaCancelled("排空阶段收到中断或停止信号，媒体刮削安全中止")
            _check_not_cancelled()

            dest_root_cache: dict[str, int] = {}
            subdir_cache: dict[tuple[int, str], int] = {}
            dir_entries_cache: dict[int, list[dict]] = {}
            moved_parent_cids: list[str] = []
            caches = (dest_root_cache, subdir_cache, dir_entries_cache, moved_parent_cids)
            protect_cids: set[str] = set()
            consec_risk = 0
            write_ops = 0

            no_rules_exit = False
            if mode == "scan_and_run":
                transfer_gate.set_media_progress({"mode": "scrape", "phase": "scanning", "current": 0, "total": 0, "label": "扫描待刮削媒体目录"})
                rules, items = await _mw().client.run_pan_thread(_mw()._scan_rules_sync, rule_ids, "scrape_enabled", settings)
                rule_cnt = len(rules)
                if not rules:
                    no_rules_exit = True
                    result_payload = {"ok": True, "batch_id": run_batch_id, "auto": 0, "need_confirm": 0, "failed": 0, "skipped": 0, "msg": "没有启用的刮削规则"}
                else:
                    total = len(items)
                    logstream.info("scrape", f"▶ 开始媒体刮削（扫描 {len(rules)} 条规则，候选 {total} 项）")
                    show_decision_cache: dict[tuple, tuple[str, list[dict]]] = {}
                    show_confirm_tid: dict[tuple, int] = {}

                    pending_rows = await asyncio.to_thread(
                        db.query,
                        "SELECT id, rule_id, media_type, file_id, status, parsed_json FROM media_tasks "
                        "WHERE task_kind='scrape' AND status IN ('need_confirm','staged','queued') ORDER BY id ASC",
                    )
                    pending_file_ids: set[str] = set()
                    for pr in pending_rows:
                        if pr["file_id"]:
                            pending_file_ids.add(str(pr["file_id"]))
                        try:
                            pj = json.loads(pr["parsed_json"] or "{}")
                            for gi in (pj.get("_grouped_items") or []):
                                if gi.get("file_id"):
                                    pending_file_ids.add(str(gi["file_id"]))
                            if (
                                pr["status"] == "need_confirm"
                                and pr["media_type"] == "tv"
                                and pj.get("title")
                                and not pj.get("tmdb_id")
                                and "_grouped_items" in pj
                            ):
                                gkey = (
                                    int(pr["rule_id"] or 0),
                                    normalize_title(str(pj["title"]), fuzzy_t2s=True),
                                    str(pj.get("year") or ""),
                                )
                                show_confirm_tid.setdefault(gkey, int(pr["id"]))
                        except Exception:
                            pass

                    for idx, it in enumerate(items, 1):
                        _check_not_cancelled()
                        pm: ParsedMedia = it["parsed"]
                        protect_cids.add(str(it.get("source_cid") or ""))
                        transfer_gate.set_media_progress({
                            "mode": "scrape", "phase": "running",
                            "current": idx, "total": total, "label": f"正在匹配: {it['orig_name']}",
                        })

                        # 如果同一 file_id 已处于待确认/暂存/排队（含剧集聚合分组内），避免重复生成孤立条目
                        if str(it["file_id"]) in pending_file_ids:
                            skip_cnt += 1
                            continue

                        show_group_key = None
                        if pm.media_type == "tv" and pm.title and not pm.tmdb_id:
                            show_group_key = (int(it["rule_id"]), normalize_title(pm.title, fuzzy_t2s=True), str(pm.year or ""))

                        item_snap = {
                            "file_id": it["file_id"],
                            "parent_cid": it["parent_cid"],
                            "source_cid": it["source_cid"],
                            "source_dir": it["source_dir"],
                            "dest_root": it.get("dest_root") or "",
                            "orig_name": it["orig_name"],
                            "orig_path": it["orig_path"],
                            "size": it["size"],
                            "sha1": it["sha1"],
                            "is_bdmv": bool(pm.is_bdmv),
                            "subtitles": it["subtitles"],
                            "parsed": pm.to_dict(),
                        }

                        if show_group_key and show_group_key in show_confirm_tid:
                            grp_tid = show_confirm_tid[show_group_key]
                            grp_row = await asyncio.to_thread(
                                db.query, "SELECT parsed_json FROM media_tasks WHERE id=?", (grp_tid,), one=True
                            )
                            if grp_row:
                                grp_p = json.loads(grp_row["parsed_json"] or "{}")
                                grp_items = list(grp_p.get("_grouped_items") or [])
                                grp_items.append(item_snap)
                                grp_p["_grouped_items"] = grp_items
                                await asyncio.to_thread(
                                    db.execute,
                                    "UPDATE media_tasks SET parsed_json=?, updated_at=? WHERE id=?",
                                    (json.dumps(grp_p, ensure_ascii=False), time.time(), grp_tid),
                                )
                                pending_file_ids.add(str(it["file_id"]))
                                if grp_tid in pending_map:
                                    pending_map[grp_tid]["ep_count"] = len(grp_items)
                                continue

                        parsed_snap = pm.to_dict()
                        parsed_snap["_parent_cid"] = it["parent_cid"]
                        parsed_snap["_size"] = it["size"]
                        parsed_snap["_sha1"] = it["sha1"]
                        parsed_snap["_subtitles"] = it["subtitles"]
                        if show_group_key:
                            parsed_snap["_grouped_items"] = [item_snap]
                        now = time.time()

                        tid = await asyncio.to_thread(
                            db.insert,
                            "INSERT INTO media_tasks(task_kind,rule_id,media_type,source_dir,source_cid,file_id,orig_name,orig_path,"
                            "parsed_json,status,batch_id,created_at,updated_at) VALUES('scrape',?,?,?,?,?,?,?,?,?,?,?,?)",
                            (it["rule_id"], pm.media_type, it["source_dir"], it["source_cid"], it["file_id"],
                             it["orig_name"], it["orig_path"], json.dumps(parsed_snap, ensure_ascii=False),
                             "running", run_batch_id, now, now),
                        )

                        # 同剧集同标题缓存一次 TMDB 候选打分，减少网络请求同时全剧保持同一识别基准
                        try:
                            cache_key = None
                            if pm.media_type == "tv" and pm.title and not pm.tmdb_id:
                                cache_key = (normalize_title(pm.title, fuzzy_t2s=True), pm.year, it["rule_media_type"])

                            if cache_key and cache_key in show_decision_cache:
                                decision, scored = show_decision_cache[cache_key]
                            else:
                                scored = await asyncio.to_thread(_mw().recall_and_score, pm, api_key, it["rule_media_type"], it.get("ep_stats"))
                                decision, scored = decide_match(scored, conf_th, margin_th)
                                if cache_key and scored:
                                    show_decision_cache[cache_key] = (decision, scored)
                        except Exception as te:
                            # TMDB 网络异常不能逃逸逐项保护：否则条目永久卡 running、
                            # 下一轮扫描还会为同一文件重复建任务
                            fail_cnt += 1
                            fail_items.append((it["orig_name"], str(te)))
                            await asyncio.to_thread(
                                db.execute,
                                "UPDATE media_tasks SET status='failed', error=?, updated_at=?, finished_at=? WHERE id=?",
                                (str(te)[:400], time.time(), time.time(), tid),
                            )
                            continue

                        compact = compact_candidates(scored)
                        top1 = compact[0] if compact else None

                        if decision == "auto" and top1:
                            try:
                                detail = (scored[0].get("detail") if scored else None) or await asyncio.to_thread(
                                    _mw().get_tmdb_detail, top1["kind"], int(top1["tmdb_id"]), api_key
                                )
                            except Exception as te:
                                fail_cnt += 1
                                fail_items.append((it["orig_name"], str(te)))
                                await asyncio.to_thread(
                                    db.execute,
                                    "UPDATE media_tasks SET status='failed', error=?, updated_at=?, finished_at=? WHERE id=?",
                                    (str(te)[:400], time.time(), time.time(), tid),
                                )
                                continue
                            if detail:
                                task_row = await asyncio.to_thread(db.query, "SELECT * FROM media_tasks WHERE id=?", (tid,), one=True)
                                try:
                                    placed = await _finalize_scraped_task(dict(task_row), detail, top1["kind"], settings, run_batch_id, api_key, caches)
                                    consec_risk = 0
                                    if placed.get("status") == "skipped":
                                        skip_cnt += 1
                                    else:
                                        auto_cnt += 1
                                        write_ops += 1
                                        auto_items.append(_placed_to_report_item(placed, it["orig_name"]))
                                    await asyncio.to_thread(
                                        db.execute,
                                        "UPDATE media_tasks SET confidence=?, candidates_json=? WHERE id=?",
                                        (float(top1.get("score") or 0), json.dumps(compact, ensure_ascii=False), tid),
                                    )
                                    if idx < total and placed.get("status") != "skipped":
                                        await _pacing_sleep(settings)
                                        await _interruptible_batch_rest(settings, write_ops, "scrape")
                                    continue
                                except _RiskCircuitBreak:
                                    fail_cnt += 1
                                    fail_items.append((it["orig_name"], "触发 115 风控熔断"))
                                    await asyncio.to_thread(
                                        db.execute,
                                        "UPDATE media_tasks SET status='failed', error='触发 115 风控熔断', updated_at=?, finished_at=? WHERE id=?",
                                        (time.time(), time.time(), tid),
                                    )
                                    raise
                                except MediaCancelled:
                                    await asyncio.to_thread(
                                        db.execute,
                                        "UPDATE media_tasks SET status='failed', error='已安全中断', updated_at=?, finished_at=? WHERE id=?",
                                        (time.time(), time.time(), tid),
                                    )
                                    raise
                                except Exception as fe:
                                    fail_cnt += 1
                                    fail_items.append((it["orig_name"], str(fe)))
                                    await asyncio.to_thread(
                                        db.execute,
                                        "UPDATE media_tasks SET status='failed', error=?, updated_at=?, finished_at=? WHERE id=?",
                                        (str(fe)[:400], time.time(), time.time(), tid),
                                    )
                                    if _is_risk_error(fe):
                                        consec_risk += 1
                                        if consec_risk >= 2:
                                            logstream.error("scrape", "🚨 媒体刮削连续触发 2 次 115 风控拦截，已立即安全熔断并恢复主程序队列！")
                                            raise _RiskCircuitBreak("连续触发 115 风控限制，媒体刮削已安全熔断")
                                    else:
                                        consec_risk = 0
                                    continue

                        # 默认 0 秒直接跳过：立即挂起为 need_confirm 并附带 Top 候选，交网页「待确认列表」处理
                        confirm_cnt += 1
                        pending_file_ids.add(str(it["file_id"]))
                        if show_group_key:
                            show_confirm_tid[show_group_key] = tid
                        pending_map[tid] = {
                            "title": pm.title or it["orig_name"],
                            "year": str(pm.year or ""),
                            "orig_name": it["orig_name"],
                            "media_type": "tv" if pm.media_type == "tv" else "movie",
                            "ep_count": 1,
                            "top1": top1,
                        }
                        await asyncio.to_thread(
                            db.execute,
                            "UPDATE media_tasks SET status='need_confirm', confidence=?, candidates_json=?, "
                            "updated_at=? WHERE id=?",
                            (
                                float(top1.get("score") or 0) if top1 else 0.0,
                                json.dumps(compact, ensure_ascii=False),
                                time.time(),
                                tid,
                            ),
                        )

            # 消费全部状态为 queued 的待执行刮削任务（含人工提交队列与重启恢复队列）
            if not no_rules_exit:
                while True:
                    _check_not_cancelled()
                    _mw()._COMMIT_WAKEUP_REQUESTED = False
                    q_row = await asyncio.to_thread(
                        db.query,
                        "SELECT * FROM media_tasks WHERE task_kind='scrape' AND status='queued' ORDER BY id ASC LIMIT 1",
                        one=True,
                    )
                    if not q_row:
                        break
                    qr = dict(q_row)
                    protect_cids.add(str(qr.get("source_cid") or ""))
                    await asyncio.to_thread(db.execute, "UPDATE media_tasks SET status='running', updated_at=? WHERE id=?", (time.time(), qr["id"]))
                    transfer_gate.set_media_progress({
                        "mode": "scrape", "phase": "running",
                        "current": auto_cnt + 1, "total": auto_cnt + 1, "label": f"执行已确认: {qr.get('selected_title') or qr['orig_name']}",
                    })
                    try:
                        sel_id = int(qr.get("selected_tmdb_id") or 0)
                        sel_kind = str(qr.get("selected_kind") or qr.get("media_type") or "movie")
                        if not sel_id:
                            raise RuntimeError("未指定有效 TMDB ID")
                        det = await asyncio.to_thread(_mw().get_tmdb_detail, sel_kind, sel_id, api_key)
                        if not det:
                            raise RuntimeError(f"拉取 TMDB/{sel_kind}/{sel_id} 详情失败")
                        placed = await _finalize_scraped_task(qr, det, sel_kind, settings, run_batch_id, api_key, caches)
                        consec_risk = 0
                        if placed.get("status") == "skipped":
                            skip_cnt += 1
                        else:
                            auto_cnt += 1
                            write_ops += 1
                            auto_items.append(_placed_to_report_item(placed, str(qr.get("orig_name") or "")))
                            await _pacing_sleep(settings)
                            await _interruptible_batch_rest(settings, write_ops, "scrape")
                    except _RiskCircuitBreak:
                        fail_cnt += 1
                        fail_items.append((str(qr.get("selected_title") or qr.get("orig_name") or ""), "触发 115 风控熔断"))
                        await asyncio.to_thread(db.execute, "UPDATE media_tasks SET status='queued', updated_at=? WHERE id=?", (time.time(), qr["id"]))
                        raise
                    except MediaCancelled:
                        await asyncio.to_thread(db.execute, "UPDATE media_tasks SET status='queued', updated_at=? WHERE id=?", (time.time(), qr["id"]))
                        raise
                    except Exception as qe:
                        fail_cnt += 1
                        fail_items.append((str(qr.get("selected_title") or qr.get("orig_name") or ""), str(qe)))
                        await asyncio.to_thread(
                            db.execute,
                            "UPDATE media_tasks SET status='failed', error=?, updated_at=?, finished_at=? WHERE id=?",
                            (str(qe)[:400], time.time(), time.time(), qr["id"]),
                        )
                        if _is_risk_error(qe):
                            consec_risk += 1
                            if consec_risk >= 2:
                                logstream.error("scrape", "🚨 刮削已提交队列连续触发 2 次 115 风控拦截，已安全熔断并恢复主程序！")
                                raise _RiskCircuitBreak("连续触发 115 风控限制，刮削队列已安全熔断")
                        else:
                            consec_risk = 0

                if settings.get("media_clean_empty_dirs", True) and moved_parent_cids:
                    removed_dirs = await _mw().client.run_pan_thread(_mw()._clean_empty_dirs_sync, moved_parent_cids, protect_cids)
                    if removed_dirs:
                        logstream.info("scrape", f"🧹 已清理 {removed_dirs} 个已搬空的旧目录")

                await asyncio.to_thread(db.set_setting, "media_scrape_last_run", time.time())
                logstream.info("scrape", f"✅ 媒体刮削批次结束：完成 {auto_cnt}，待确认 {confirm_cnt}，失败 {fail_cnt}")
                result_payload = {
                    "ok": True,
                    "batch_id": run_batch_id,
                    "auto": auto_cnt,
                    "need_confirm": confirm_cnt,
                    "failed": fail_cnt,
                    "skipped": skip_cnt,
                }

    except _RiskCircuitBreak as rc:
        outcome = "risk_aborted"
        outcome_msg = str(rc)
        logstream.error("scrape", f"🛑 {rc}")
        result_payload = {"ok": False, "risk_aborted": True, "msg": str(rc), "auto": auto_cnt, "need_confirm": confirm_cnt, "failed": fail_cnt}
    except MediaCancelled as mc:
        outcome = "cancelled"
        outcome_msg = str(mc)
        logstream.warn("scrape", f"⏸ {mc}")
        result_payload = {"ok": False, "cancelled": True, "msg": str(mc), "auto": auto_cnt, "need_confirm": confirm_cnt, "failed": fail_cnt}
    except Exception as e:
        outcome = "error"
        outcome_msg = str(e)
        logstream.error("scrape", f"媒体刮削异常: {logstream.exc(e)}")
        result_payload = {"ok": False, "msg": str(e)}
    finally:
        # 主程序转存与云下载先于通知立即恢复运行（同步调用防门闸复位竞态，口径同 organize）
        transfer_gate.clear_cancel()
        transfer_gate.resume_all()
        rerun_committed = bool(_mw()._COMMIT_WAKEUP_REQUESTED and outcome == "done" and is_system_running())
        _mw()._COMMIT_WAKEUP_REQUESTED = False
        if _mw()._ACTIVE_TASK_REF.get("scrape") is asyncio.current_task():
            _mw()._ACTIVE_TASK_REF["scrape"] = None

    elapsed_sec = int(round(time.monotonic() - started_at))
    tg_text = _build_scrape_tg_report(
        mode=mode,
        outcome=outcome,
        elapsed_sec=elapsed_sec,
        rule_cnt=rule_cnt,
        total_candidates=total,
        auto_cnt=auto_cnt,
        confirm_cnt=confirm_cnt,
        skip_cnt=skip_cnt,
        fail_cnt=fail_cnt,
        removed_dirs=removed_dirs,
        auto_items=auto_items,
        pending_items=list(pending_map.values()),
        fail_items=fail_items,
        error_msg=outcome_msg,
        system_running=is_system_running(),
    )
    if tg_text:
        jobs.enqueue_tg_message(tg_text, gap_sec=1.0)

    if rerun_committed:
        try:
            asyncio.get_running_loop().call_soon(_mw().schedule_commit_runner_if_idle)
        except RuntimeError:
            pass

    return result_payload
