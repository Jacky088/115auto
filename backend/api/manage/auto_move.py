"""定时文件整理（自动分流）规则、预览任务与整理账本撤销路由。"""
import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException

from ... import database as db
from ...core import logstream, transfer_gate
from ...pan115 import client as pan
from ...worker import jobs, scan_index
from ..deps import require_user
from ._common import _clamp_page, _like_escape

router = APIRouter()


# ---------- auto_move (定时文件整理) ----------
@router.get("/auto-move/rules")
def get_auto_move_rules(_=Depends(require_user)):
    rules = [dict(r) for r in db.query("SELECT * FROM auto_move_rules ORDER BY id ASC")]
    raw_ft = db.get_setting("auto_move_fixed_times", '["06:00", "12:00", "18:00"]')
    try:
        fixed_times = json.loads(raw_ft) if isinstance(raw_ft, str) else raw_ft
        if not isinstance(fixed_times, list):
            fixed_times = ["06:00", "12:00", "18:00"]
    except Exception:
        fixed_times = ["06:00", "12:00", "18:00"]

    return {
        "system_running": str(db.get_setting("running", False)).lower() == "true",
        "rules": rules,
        "cloud_dirs": jobs.cloud_download_dirs(),
        "settings": {
            "auto_move_enabled": bool(db.get_setting("auto_move_enabled", False)),
            "auto_move_schedule_type": str(db.get_setting("auto_move_schedule_type", "interval") or "interval"),
            "auto_move_interval_hours": int(db.get_setting("auto_move_interval_hours", 6) or 6),
            "auto_move_fixed_times": fixed_times,
            "auto_move_scan_subdirs": bool(db.get_setting("auto_move_scan_subdirs", True)),
            "auto_move_notify": bool(db.get_setting("auto_move_notify", True)),
            "auto_move_last_run": float(db.get_setting("auto_move_last_run", 0) or 0),
            "auto_move_mode": str(db.get_setting("auto_move_mode", "library") or "library"),
            "auto_move_season_folder": bool(db.get_setting("auto_move_season_folder", True)),
            "auto_move_fuzzy_t2s": bool(db.get_setting("auto_move_fuzzy_t2s", True)),
            "auto_move_dedup_by_episode": bool(db.get_setting("auto_move_dedup_by_episode", True)),
        },
        "is_organizing": transfer_gate.is_auto_move_running(),
        "active_mode": transfer_gate.get_exclusive_status().get("active_mode", "none"),
        "exclusive_busy": transfer_gate.is_exclusive_busy(),
        "gate_state": transfer_gate.get_gate_state(),
        "drain_status": transfer_gate.get_drain_status(),
        "next_runs": jobs.compute_next_runs(),
        "last_result": jobs.get_last_auto_move_result(),
    }


@router.post("/auto-move/rules")
def save_auto_move_rule(body: dict, _=Depends(require_user)):
    source_dir = str(body.get("source_dir", "")).strip().rstrip("/")
    target_dir = str(body.get("target_dir", "")).strip().rstrip("/")
    if not source_dir or not target_dir:
        raise HTTPException(400, "源目录和目标目录不能为空")

    mode = str(body.get("mode", "default")).strip().lower()
    if mode not in ("default", "library", "exact"):
        mode = "default"

    # 媒体类型守卫：movie 规则跳过带集数标识文件（剧集留给剧集规则），auto 为历史行为
    media_type = str(body.get("media_type", "auto")).strip().lower()
    if media_type not in ("auto", "movie", "tv"):
        media_type = "auto"

    # 按父目录名匹配（云下载模式）保存校验：源目录必须是当前配置的云下载目录，
    # 防止把整目录匹配语义误用在普通目录上（结构性保证，不靠用户自觉）
    dir_match = 1 if body.get("dir_match") else 0
    if dir_match:
        cd_dirs = jobs.cloud_download_dirs()
        if source_dir not in cd_dirs:
            raise HTTPException(
                400,
                f"源目录 {source_dir} 不是任何分类配置的云下载目录（当前：{'、'.join(cd_dirs) or '未配置'}）。"
                "请先在「转存整理」页把对应分类的云下载目录设为该目录，或修改源目录后再开启此模式。")

    raw_kw = str(body.get("keyword", "")).strip()
    extracted_kw = pan.extract_target_folder_name(target_dir)
    keyword = raw_kw or extracted_kw
    if mode == "exact" and not keyword:
        raise HTTPException(400, "精确模式下目标目录未包含有效文件夹名称，无法提取匹配关键词，不予保存")

    allow_english = 1 if body.get("allow_english") else 0
    enabled = 1 if body.get("enabled", True) else 0
    rule_id = body.get("id")
    if rule_id:
        db.execute(
            "UPDATE auto_move_rules SET source_dir=?, target_dir=?, keyword=?, mode=?, media_type=?, dir_match=?, allow_english=?, enabled=? WHERE id=?",
            (source_dir, target_dir, keyword, mode, media_type, dir_match, allow_english, enabled, rule_id)
        )
    else:
        db.execute(
            "INSERT INTO auto_move_rules(source_dir, target_dir, keyword, mode, media_type, dir_match, allow_english, enabled, created_at, last_run_at, last_moved_count) "
            "VALUES(?,?,?,?,?,?,?,?,?,NULL,0)",
            (source_dir, target_dir, keyword, mode, media_type, dir_match, allow_english, enabled, time.time())
        )
    mode_desc = "总库分流" if mode == "library" else ("精确匹配" if mode == "exact" else "跟随全局")
    type_desc = {"movie": "，电影库", "tv": "，剧集库"}.get(media_type, "")
    logstream.info("auto_move", f"已保存整理规则 [{source_dir} ➔ {target_dir} / {mode_desc}{type_desc}] (关键词: {keyword or '自动'})")
    return {"ok": True}


@router.delete("/auto-move/rules/{rule_id}")
def delete_auto_move_rule(rule_id: int, _=Depends(require_user)):
    db.execute("DELETE FROM auto_move_rules WHERE id=?", (rule_id,))
    logstream.info("auto_move", f"已删除整理规则 #{rule_id}")
    rem = db.query("SELECT count(*) c FROM auto_move_rules WHERE enabled=1", one=True)
    if not rem or int(rem["c"]) == 0:
        if bool(db.get_setting("auto_move_enabled", False)):
            db.set_setting("auto_move_enabled", False)
            logstream.warn("auto_move", "所有启用的整理规则已清空，定时文件整理总开关已自动关闭")
    return {"ok": True}


@router.put("/auto-move/settings")
def update_auto_move_settings(body: dict, _=Depends(require_user)):
    sys_running = str(db.get_setting("running", False)).lower() == "true"

    # 提取目标调度类型和固定时段
    target_sched_type = body.get("auto_move_schedule_type", db.get_setting("auto_move_schedule_type", "interval"))

    parsed_fixed_times = None
    if "auto_move_fixed_times" in body:
        ft = body["auto_move_fixed_times"]
        if isinstance(ft, list):
            parsed_fixed_times = ft
        elif isinstance(ft, str):
            try:
                parsed_fixed_times = json.loads(ft)
            except Exception:
                parsed_fixed_times = []
    else:
        ft_raw = db.get_setting("auto_move_fixed_times", '["06:00", "12:00", "18:00"]')
        try:
            parsed_fixed_times = json.loads(ft_raw) if isinstance(ft_raw, str) else list(ft_raw)
        except Exception:
            parsed_fixed_times = []

    target_enabled = body.get("auto_move_enabled")
    if target_enabled is None:
        target_enabled = bool(db.get_setting("auto_move_enabled", False))
    else:
        target_enabled = bool(target_enabled)

    # 如果要开启总开关（或在开启状态下变更调度规则），进行闭环校验
    if target_enabled:
        if not sys_running:
            raise HTTPException(400, "系统处于停止状态，请先在顶栏点击「运行」启动系统后再开启定时整理！")

        # 1. 检查固定时间点是否为空
        if target_sched_type == "fixed_times":
            if not parsed_fixed_times or len(parsed_fixed_times) == 0:
                raise HTTPException(400, "每天固定时间点列表为空，请至少添加一个执行时段后再开启总开关！")

        # 2. 检查是否有启用的整理规则
        rule_cnt = db.query("SELECT count(*) c FROM auto_move_rules WHERE enabled=1", one=True)
        if not rule_cnt or int(rule_cnt["c"]) == 0:
            raise HTTPException(400, "当前没有启用的整理规则，请至少添加并启用一条整理规则后再开启总开关！")

    if "auto_move_enabled" in body:
        db.set_setting("auto_move_enabled", bool(body["auto_move_enabled"]))
    if "auto_move_schedule_type" in body and body["auto_move_schedule_type"] in ("interval", "fixed_times"):
        db.set_setting("auto_move_schedule_type", body["auto_move_schedule_type"])
    if "auto_move_interval_hours" in body:
        db.set_setting("auto_move_interval_hours", max(int(body["auto_move_interval_hours"] or 6), 1))
    if "auto_move_fixed_times" in body:
        ft = body["auto_move_fixed_times"]
        if isinstance(ft, list):
            db.set_setting("auto_move_fixed_times", json.dumps(ft))
        elif isinstance(ft, str):
            db.set_setting("auto_move_fixed_times", ft)
    if "auto_move_scan_subdirs" in body:
        db.set_setting("auto_move_scan_subdirs", bool(body["auto_move_scan_subdirs"]))
    if "auto_move_notify" in body:
        db.set_setting("auto_move_notify", bool(body["auto_move_notify"]))
    if "auto_move_mode" in body and body["auto_move_mode"] in ("library", "exact"):
        db.set_setting("auto_move_mode", body["auto_move_mode"])
    if "auto_move_season_folder" in body:
        db.set_setting("auto_move_season_folder", bool(body["auto_move_season_folder"]))
    if "auto_move_fuzzy_t2s" in body:
        db.set_setting("auto_move_fuzzy_t2s", bool(body["auto_move_fuzzy_t2s"]))
    if "auto_move_dedup_by_episode" in body:
        db.set_setting("auto_move_dedup_by_episode", bool(body["auto_move_dedup_by_episode"]))
    return {"ok": True, "next_runs": jobs.compute_next_runs()}


@router.post("/auto-move/run")
async def run_auto_move_now(body: dict | None = None, _=Depends(require_user)):
    include_ids: list[str] | None = None
    raw_ids = (body or {}).get("include_ids")
    if raw_ids is not None:
        if not isinstance(raw_ids, list):
            raise HTTPException(400, "include_ids 必须为文件 ID 数组")
        include_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        if not include_ids:
            raise HTTPException(400, "请至少选择一个需要执行分流整理的文件")
    if not (str(await asyncio.to_thread(db.get_setting, "running", False)).lower() == "true"):
        raise HTTPException(400, "系统处于停止状态，请先在顶栏点击「运行」启动系统后再执行！")
    if not bool(await asyncio.to_thread(db.get_setting, "auto_move_enabled", False)):
        raise HTTPException(400, "定时文件整理功能未开启，请先打开功能总开关后再执行！")

    excl = transfer_gate.get_exclusive_status()
    active_mode = excl.get("active_mode", "none")
    if excl.get("exclusive_busy") and active_mode in ("organize", "scrape", "dedupe"):
        mode_cn = {"organize": "媒体整理", "scrape": "媒体刮削", "dedupe": "重复清理"}.get(active_mode, "独占任务")
        raise HTTPException(
            409,
            f"当前正在执行「{mode_cn}」，同一时刻仅允许运行一个独占任务，请等待其完成或安全中断后再执行分流归档！",
        )

    lock = jobs._get_auto_move_lock()
    if lock.locked() or excl.get("exclusive_busy") or transfer_gate.get_gate_state() != "idle":
        return {
            "ok": True,
            "already_running": True,
            "gate_state": transfer_gate.get_gate_state(),
            "drain_status": transfer_gate.get_drain_status(),
            "msg": "整理任务已在排队执行中，正在等待队列终点并执行移动..."
        }

    # 异步启动后台执行：即便需要等待转存/云下载/重试数分钟，也不会阻塞 HTTP 接口
    asyncio.create_task(jobs.execute_auto_move(trigger="manual", include_ids=include_ids))
    return {
        "ok": True,
        "started": True,
        "gate_state": "draining",
        "drain_status": transfer_gate.get_drain_status(),
        "msg": "已触发排队整理：系统正在等待现有转存/云下载及重试全部完成，完成后将自动开启移动归档。"
    }


@router.post("/auto-move/preview")
async def preview_auto_move_now(body: dict | None = None, _=Depends(require_user)):
    """整理前预览（dry_run）：返回各启用规则「将会移动哪些文件」的计划，不做任何移动。"""
    rule_ids = (body or {}).get("rule_ids")
    if rule_ids is not None and not isinstance(rule_ids, list):
        raise HTTPException(400, "rule_ids 必须是数组")
    res = await jobs.preview_auto_move(rule_ids)
    if not res.get("ok"):
        raise HTTPException(400 if not res.get("msg", "").startswith("当前有互斥") else 409, res.get("msg", "预览失败"))
    return res


@router.post("/auto-move/preview/start")
async def preview_auto_move_start(body: dict | None = None, _=Depends(require_user)):
    """异步预览：立即返回 job_id，前端轮询 /auto-move/preview/status 显示进度。"""
    rule_ids = (body or {}).get("rule_ids")
    if rule_ids is not None and not isinstance(rule_ids, list):
        raise HTTPException(400, "rule_ids 必须是数组")
    if str(await asyncio.to_thread(db.get_setting, "running", False)).lower() != "true":
        raise HTTPException(400, "系统处于停止状态，请先在顶栏点击「运行」后再预览")
    if not bool(await asyncio.to_thread(db.get_setting, "auto_move_enabled", False)):
        raise HTTPException(400, "定时文件整理功能未开启，请先打开功能总开关")
    sql = "SELECT source_dir FROM auto_move_rules WHERE enabled=1"
    want_ids = [int(x) for x in rule_ids if str(x).isdigit()] if rule_ids else []
    if want_ids:
        sql += " AND id IN (" + ",".join("?" for _ in want_ids) + ")"
    paths = [r["source_dir"] for r in await asyncio.to_thread(db.query, sql, tuple(want_ids))]
    guard = await asyncio.to_thread(scan_index.scope_guard, paths, bool((body or {}).get("confirm_large")))
    if guard:
        return guard
    res = scan_index.start_job(
        "auto_move_preview", lambda ctx: jobs.preview_auto_move(rule_ids, ctx))
    if not res.get("ok"):
        raise HTTPException(409, res.get("msg") or "扫描任务发起失败")
    return res


@router.get("/auto-move/preview/status")
async def preview_auto_move_status(job_id: str, _=Depends(require_user)):
    return scan_index.job_status(job_id)


@router.post("/auto-move/preview/cancel")
async def preview_auto_move_cancel(body: dict | None = None, _=Depends(require_user)):
    return scan_index.cancel_job(str((body or {}).get("job_id") or ""))


@router.get("/auto-move/records")
def list_auto_move_records(rule_id: int = 0, undone: str = "all", batch_id: str = "",
                           keyword: str = "", page: int = 1, size: int = 50,
                           _=Depends(require_user)):
    """整理账本（定时文件整理的移动记录）：平铺分页 + 批次列表。"""
    where, params = ["action='auto_move'"], []
    if rule_id > 0:
        where.append("task_id=?")
        params.append(int(rule_id))
    if undone in ("0", "1"):
        where.append("undone=?")
        params.append(int(undone))
    if batch_id:
        where.append("batch_id=?")
        params.append(batch_id.strip()[:64])
    if keyword.strip():
        esc = _like_escape(keyword.strip()[:100])
        where.append("(orig_name LIKE ? ESCAPE '\\' OR dest_path LIKE ? ESCAPE '\\')")
        params += [f"%{esc}%", f"%{esc}%"]
    page_n, size_n = _clamp_page(page, size, 200)
    w = " AND ".join(where)
    total = db.query(f"SELECT count(*) c FROM organized_records WHERE {w}", tuple(params), one=True)["c"]
    rows = db.query(
        f"SELECT * FROM organized_records WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params + [size_n, (page_n - 1) * size_n]))
    rule_rows = db.query("SELECT id, source_dir, target_dir, keyword, mode FROM auto_move_rules")
    rule_map = {r["id"]: r for r in (dict(x) for x in rule_rows)}
    items = []
    for r in rows:
        d = dict(r)
        rr = rule_map.get(int(d["task_id"] or 0))
        d["rule_label"] = (f"{rr['source_dir']} ➔ {rr['target_dir']}" if rr else "规则已删除")
        items.append(d)
    batches = [dict(b) for b in db.query(
        "SELECT batch_id, count(*) c, min(created_at) t0, max(created_at) t1,"
        " sum(CASE WHEN undone=0 THEN 1 ELSE 0 END) alive,"
        " GROUP_CONCAT(DISTINCT task_id) rule_ids"
        " FROM organized_records WHERE action='auto_move' GROUP BY batch_id ORDER BY t0 DESC LIMIT 60")]
    return {"total": total, "items": items, "page": page_n, "size": size_n, "batches": batches}


@router.post("/auto-move/records/undo")
async def undo_auto_move_records(body: dict, _=Depends(require_user)):
    """撤销整理记录（单条 record_id 或整批 batch_id），可选 stop_rule_ids 同时停用涉及规则。

    撤销会把文件移回源目录；若不停用规则，下一轮定时整理命中后会再次移走——
    前端在确认弹窗中向用户说明该后果并由用户勾选是否停用。
    """
    record_id = int(body.get("record_id") or 0)
    batch_id = str(body.get("batch_id") or "").strip()
    stop_rule_ids = body.get("stop_rule_ids")
    if stop_rule_ids is not None and not isinstance(stop_rule_ids, list):
        raise HTTPException(400, "stop_rule_ids 必须是数组")
    res = await jobs.undo_auto_move_records(record_id, batch_id, stop_rule_ids)
    if not res.get("ok") and res.get("msg"):
        # 无可撤销记录/独占忙碌等业务原因：独占忙碌按 409，其余按 400
        code = 409 if "互斥" in res["msg"] else 400
        raise HTTPException(code, res["msg"])
    return res
