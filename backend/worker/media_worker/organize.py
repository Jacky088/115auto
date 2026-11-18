"""目录整理主流程：只读预览、独占串行执行队列与操作账本安全撤销。"""
from __future__ import annotations

import asyncio
import json
import posixpath
import time
import uuid

from ... import database as db
from ...core import logstream, transfer_gate
from ...core.media_parser import ParsedMedia, format_media_target
from .. import jobs
from .common import (
    MediaCancelled,
    _RiskCircuitBreak,
    _check_not_cancelled,
    _get_lock,
    _interruptible_batch_rest,
    _is_risk_error,
    _pacing_sleep,
    _split_rel_and_name,
    get_media_settings,
    is_system_running,
)
from .reports import _build_organize_tg_report

def _mw():
    """延迟获取 media_worker 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import media_worker
    return media_worker


async def preview_organize(rule_ids: list[int] | None = None, ctx=None) -> dict:
    """Dry-Run 预览待整理条目（只读 115 目录树与本地规则计算，零网盘写操作）。

    ctx 由异步预览任务传入：走扫描索引增量 + 预算控制；不传则实时全量扫描。
    """
    if transfer_gate.is_exclusive_busy():
        return {"ok": False, "msg": "当前有独占写任务正在执行，请待结束后再预览", "items": []}
    settings = await asyncio.to_thread(get_media_settings)
    scan_stats: dict = {}
    rules, items = await _mw().client.run_pan_thread(
        _mw()._scan_rules_sync, rule_ids, "organize_enabled", settings, False, scan_stats, ctx)
    preview_rows = []
    for it in items:
        pm: ParsedMedia = it["parsed"]
        rel_dir, fname = format_media_target(pm, settings)
        dest_dir, final_name = _split_rel_and_name(it["dest_root"], rel_dir, fname)
        target_full = f"{dest_dir.rstrip('/')}/{final_name}"
        already_ok = (it["orig_path"].rstrip("/") == target_full)
        preview_rows.append({
            "rule_id": it["rule_id"],
            "rule_name": it["rule_name"],
            "file_id": it["file_id"],
            "orig_name": it["orig_name"],
            "orig_path": it["orig_path"],
            "target_path": dest_dir,
            "target_name": final_name,
            "media_type": pm.media_type,
            "parsed_title": pm.title,
            "year": pm.year,
            "season": pm.season,
            "episode": pm.episode,
            "tmdb_id": pm.tmdb_id,
            "is_bdmv": it["is_bdmv"],
            "subtitle_count": len(it.get("subtitles") or []),
            "already_ok": already_ok,
        })
    return {
        "ok": True,
        "rule_count": len(rules),
        "total": len(preview_rows),
        "pending_count": sum(1 for r in preview_rows if not r["already_ok"]),
        "items": preview_rows,
        "scan_stats": scan_stats,
        "truncated": bool(scan_stats.get("truncated")),
    }

async def execute_organize_queue(rule_ids: list[int] | None = None, trigger: str = "manual",
                                 include_ids: list[str] | None = None) -> dict:
    """模块 A：目录整理严格单队列顺序执行。

    include_ids 非 None 时，仅处理预览阶段用户明确勾选的 file_id（预览后新增文件不处理）。
    """
    if not is_system_running():
        return {"ok": False, "msg": "系统处于停止状态，请先点击顶栏「运行」启动系统"}

    lock = _get_lock("organize")
    if lock.locked():
        return {"ok": False, "msg": "目录整理正在执行中，请勿重复发起"}

    started_at = time.monotonic()
    settings = await asyncio.to_thread(get_media_settings)

    ok, busy_msg = transfer_gate.try_begin_exclusive("organize")
    if not ok:
        return {"ok": False, "msg": busy_msg}

    batch_id = f"org_{uuid.uuid4().hex[:10]}"
    done_cnt = 0
    skip_cnt = 0
    fail_cnt = 0
    rule_cnt = 0
    total = 0
    removed_dirs = 0
    organize_scan_stats: dict = {}
    done_items: list[dict] = []
    fail_items: list[tuple[str, str]] = []
    outcome = "done"
    outcome_msg = ""
    result_payload: dict

    try:
        async with lock:
            transfer_gate.clear_cancel()
            transfer_gate.set_media_progress({"mode": "organize", "phase": "draining", "current": 0, "total": 0, "label": "等待转存队列消化"})
            drained = await transfer_gate.prepare_and_drain_transfers(mode="organize")
            if drained is False:
                raise MediaCancelled("排空阶段收到中断或停止信号，目录整理安全中止")
            _check_not_cancelled()

            transfer_gate.set_media_progress({"mode": "organize", "phase": "scanning", "current": 0, "total": 0, "label": "正在扫描待整理目录"})
            rules, items = await _mw().client.run_pan_thread(
                _mw()._scan_rules_sync, rule_ids, "organize_enabled", settings, False, organize_scan_stats)
            if include_ids is not None:
                wanted = {str(x) for x in include_ids}
                items = [it for it in items if str(it.get("file_id")) in wanted]
            rule_cnt = len(rules)
            total = len(items)
            if not rules:
                result_payload = {
                    "ok": True, "batch_id": batch_id, "total": 0, "done": 0, "skipped": 0, "failed": 0,
                    "msg": "没有启用的整理规则",
                }
            else:
                logstream.info("organize", f"▶ 开始执行目录整理（触发：{trigger}，规则 {len(rules)} 条，候选 {total} 项）")
                dest_root_cache: dict[str, int] = {}
                subdir_cache: dict[tuple[int, str], int] = {}
                dir_entries_cache: dict[int, list[dict]] = {}
                moved_parent_cids: list[str] = []
                protect_cids = {str(it.get("source_cid") or "") for it in items}
                rule_counts: dict[int, int] = {}
                consec_risk = 0
                write_ops = 0

                for idx, it in enumerate(items, 1):
                    _check_not_cancelled()
                    pm: ParsedMedia = it["parsed"]
                    rel_dir, fname = format_media_target(pm, settings)
                    full_dest_dir, final_name = _split_rel_and_name(it["dest_root"], rel_dir, fname)
                    transfer_gate.set_media_progress({
                        "mode": "organize", "phase": "running",
                        "current": idx, "total": total, "label": f"正在整理: {it['orig_name']}",
                    })

                    now = time.time()
                    tid = await asyncio.to_thread(
                        db.insert,
                        "INSERT INTO media_tasks(task_kind,rule_id,media_type,source_dir,source_cid,file_id,orig_name,orig_path,"
                        "parsed_json,target_path,target_name,status,batch_id,created_at,updated_at) VALUES('organize',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (it["rule_id"], pm.media_type, it["source_dir"], it["source_cid"], it["file_id"],
                         it["orig_name"], it["orig_path"], json.dumps(pm.to_dict(), ensure_ascii=False),
                         full_dest_dir, final_name, "running", batch_id, now, now),
                    )

                    try:
                        res = await _mw().client.run_pan_thread(
                            _mw()._apply_media_placement_sync,
                            it, rel_dir, fname, settings, batch_id, tid, pm.tmdb_id,
                            dest_root_cache, subdir_cache, dir_entries_cache, moved_parent_cids,
                        )
                        st = res["status"]
                        consec_risk = 0
                        if st == "done":
                            done_cnt += 1
                            write_ops += 1
                            rule_counts[it["rule_id"]] = rule_counts.get(it["rule_id"], 0) + 1
                            done_items.append({
                                "title": pm.title or it["orig_name"],
                                "year": str(pm.year or ""),
                                "tmdb_id": pm.tmdb_id,
                                "media_type": pm.media_type,
                                "dest_path": res["dest_path"],
                                "dest_name": res["dest_name"],
                                "sub_count": int(res.get("subtitles_moved") or 0),
                                "is_bdmv": bool(it.get("is_bdmv")),
                            })
                        else:
                            skip_cnt += 1
                        await asyncio.to_thread(
                            db.execute,
                            "UPDATE media_tasks SET status=?, target_path=?, target_name=?, error=?, updated_at=?, finished_at=? WHERE id=?",
                            (st, res["dest_path"], res["dest_name"], res.get("reason", ""), time.time(), time.time(), tid),
                        )
                    except Exception as e:
                        fail_cnt += 1
                        fail_items.append((it["orig_name"], str(e)))
                        logstream.warn("organize", f"整理失败 {it['orig_name']}: {e}")
                        await asyncio.to_thread(
                            db.execute,
                            "UPDATE media_tasks SET status='failed', error=?, updated_at=?, finished_at=? WHERE id=?",
                            (str(e)[:400], time.time(), time.time(), tid),
                        )
                        if _is_risk_error(e):
                            consec_risk += 1
                            if consec_risk >= 2:
                                alert_msg = "🚨 目录整理连续触发 2 次 115 风控拦截，已立即安全熔断并恢复主程序队列！"
                                logstream.error("organize", alert_msg)
                                raise _RiskCircuitBreak("连续触发 2 次 115 风控限制，目录整理已安全熔断")
                        else:
                            consec_risk = 0

                    if idx < total:
                        await _pacing_sleep(settings)
                        await _interruptible_batch_rest(settings, write_ops, "organize")

                if settings.get("media_clean_empty_dirs", True) and moved_parent_cids:
                    removed_dirs = await _mw().client.run_pan_thread(_mw()._clean_empty_dirs_sync, moved_parent_cids, protect_cids)
                    if removed_dirs:
                        logstream.info("organize", f"🧹 已清理 {removed_dirs} 个已搬空的旧目录")

                now = time.time()
                for r in rules:
                    await asyncio.to_thread(
                        db.execute,
                        "UPDATE media_rules SET last_run_at=?, last_count=? WHERE id=?",
                        (now, rule_counts.get(int(r["id"]), 0), r["id"]),
                    )
                await asyncio.to_thread(db.set_setting, "media_organize_last_run", now)

                logstream.info("organize", f"✅ 目录整理完成：成功 {done_cnt}，跳过 {skip_cnt}，失败 {fail_cnt}")
                result_payload = {
                    "ok": True, "batch_id": batch_id, "total": total,
                    "done": done_cnt, "skipped": skip_cnt, "failed": fail_cnt,
                }

    except _RiskCircuitBreak as rc:
        outcome = "risk_aborted"
        outcome_msg = str(rc)
        logstream.error("organize", f"🛑 {rc}")
        result_payload = {
            "ok": False, "risk_aborted": True, "msg": str(rc), "batch_id": batch_id,
            "done": done_cnt, "skipped": skip_cnt, "failed": fail_cnt,
        }
    except MediaCancelled as mc:
        outcome = "cancelled"
        outcome_msg = str(mc)
        logstream.warn("organize", f"⏸ {mc}")
        result_payload = {
            "ok": False, "cancelled": True, "msg": str(mc), "batch_id": batch_id,
            "done": done_cnt, "skipped": skip_cnt, "failed": fail_cnt,
        }
    except Exception as e:
        outcome = "error"
        outcome_msg = str(e)
        logstream.error("organize", f"目录整理异常: {logstream.exc(e)}")
        result_payload = {"ok": False, "msg": str(e), "batch_id": batch_id}
    finally:
        if _mw()._ACTIVE_TASK_REF.get("organize") is asyncio.current_task():
            _mw()._ACTIVE_TASK_REF["organize"] = None
        transfer_gate.clear_cancel()
        # 同步调用（不 await to_thread）：resume 内部只有短暂锁与事件 set；
        # 若在此让出事件循环，新独占任务可能恰好 begin_exclusive 成功后被旧任务的
        # resume 误复位门闸（jobs.auto_move 的 finally 亦为同步调用，口径一致）
        transfer_gate.resume_all()

    elapsed_sec = int(round(time.monotonic() - started_at))
    tg_text = _build_organize_tg_report(
        outcome=outcome,
        scan_stats=organize_scan_stats,
        trigger=trigger,
        batch_id=batch_id,
        elapsed_sec=elapsed_sec,
        rule_cnt=rule_cnt,
        total_candidates=total,
        done_cnt=done_cnt,
        skip_cnt=skip_cnt,
        fail_cnt=fail_cnt,
        removed_dirs=removed_dirs,
        done_items=done_items,
        fail_items=fail_items,
        error_msg=outcome_msg,
        system_running=is_system_running(),
    )
    if tg_text:
        jobs.enqueue_tg_message(tg_text, gap_sec=1.0)

    return result_payload

def _undo_records_sync(records: list[dict]) -> tuple[int, int]:
    """同步撤销 `organized_records` 记录（恢复原位置与原文件名；若原目录已被清理则自动重建）。"""
    undone = 0
    failed = 0
    recreated_cids: dict[str, int] = {}
    for rec in records:
        try:
            fid = str(rec["file_id"])
            orig_cid = int(str(rec.get("parent_cid") or "0") or 0)
            dest_cid = int(str(rec.get("dest_cid") or "0") or 0)
            orig_name = str(rec["orig_name"])
            dest_name = str(rec["dest_name"])
            orig_parent_dir = posixpath.dirname(str(rec.get("orig_path") or ""))

            def _ensure_orig_cid() -> int:
                if orig_parent_dir and orig_parent_dir != "/":
                    cid = recreated_cids.get(orig_parent_dir) or int(_mw().client.mkdirs(orig_parent_dir))
                    recreated_cids[orig_parent_dir] = cid
                    return cid
                return 0

            if not orig_cid:
                orig_cid = _ensure_orig_cid()
            if not orig_cid:
                raise RuntimeError("缺少原始目录 cid")

            if dest_cid != orig_cid:
                try:
                    _mw().client.move_entries([fid], orig_cid)
                except Exception as move_err:
                    # 批末空目录清理可能已删除原目录 cid，按 orig_path 逐级重建后再重试移动
                    recreated = _ensure_orig_cid()
                    if recreated and recreated != orig_cid:
                        orig_cid = recreated
                        _mw().client.move_entries([fid], orig_cid)
                    else:
                        raise move_err
            if dest_name != orig_name:
                _mw().client.rename_entry(fid, orig_name)
            db.execute("UPDATE organized_records SET undone=1 WHERE id=?", (rec["id"],))
            undone += 1
            time.sleep(0.6 * _mw().client.pace_factor())
        except Exception as e:
            failed += 1
            logstream.warn("organize", f"撤销记录 #{rec['id']}({rec['dest_name']}) 失败: {e}")
    return undone, failed


async def undo_organized(batch_id: str = "", record_id: int = 0) -> dict:
    """按单条记录或整个批次安全撤销已移动/重命名的网盘文件。"""
    if transfer_gate.is_exclusive_busy():
        return {"ok": False, "msg": "当前正在执行整理或刮削，请待结束后再执行撤销"}
    if not record_id and not batch_id:
        return {"ok": False, "msg": "请指定需要撤销的记录 ID 或批次 ID"}

    if record_id:
        rows = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM organized_records WHERE id=? AND undone=0", (int(record_id),)
        )]
    else:
        rows = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM organized_records WHERE batch_id=? AND undone=0 ORDER BY id DESC", (batch_id,)
        )]
    if not rows:
        return {"ok": False, "msg": "没有找到可撤销的未回退记录"}

    undone, failed = await _mw().client.run_pan_thread(_undo_records_sync, rows)
    return {"ok": undone > 0, "undone": undone, "failed": failed, "msg": f"已撤销 {undone} 条，失败 {failed} 条"}
