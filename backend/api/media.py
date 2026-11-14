"""整理刮削 REST API（规则管理、Dry-Run 预览、独占任务调度、人工核对暂存/提交、账本撤销与本地元数据查看）。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from .. import auth, config, database as db
from ..core import logstream, tmdb_scraper, transfer_gate
from ..core.media_parser import validate_organize_template
from ..worker import media_worker
from .manage import require_user

_APP_BACKEND_DIR = Path(__file__).resolve().parents[1]

router = APIRouter(prefix="/api/media", dependencies=[Depends(require_user)])
poster_router = APIRouter(prefix="/api/media")


def _require_poster_or_user(
    authorization: str | None = Header(default=None),
    sa_poster: str | None = Cookie(default=None),
    sa_auth: str | None = Cookie(default=None),
):
    """兼容 `<img>` 标签鉴权：Bearer Token / 海报专用 Cookie / 登录态 Cookie 任一有效即可。"""
    candidates = []
    if authorization and authorization.lower().startswith("bearer "):
        candidates.append(authorization.split(None, 1)[1].strip())
    if sa_poster:
        candidates.append(sa_poster.strip())
    if sa_auth:
        candidates.append(sa_auth.strip())
    if not any(auth.check_token(tok) for tok in candidates if tok):
        raise HTTPException(401, "未登录或海报访问凭证已过期")


# ---------- 请求模型 ----------

class RuleIn(BaseModel):
    name: str = ""
    source_dir: str
    target_dir: str = ""
    media_type: str = "auto"   # auto | movie | tv
    organize_enabled: bool = True
    scrape_enabled: bool = True
    enabled: bool = True


class RuleIdsIn(BaseModel):
    rule_ids: list[int] | None = None


class MediaSettingsIn(BaseModel):
    media_append_tmdb_tag: bool | None = None
    media_organize_template_movie: str | None = None
    media_organize_template_tv: str | None = None
    media_conflict_policy: str | None = None
    media_move_subtitles: bool | None = None
    media_skip_extras: bool | None = None
    media_extra_min_mb: int | None = None
    media_clean_empty_dirs: bool | None = None
    media_scrape_confidence_threshold: int | None = None
    media_scrape_margin_threshold: int | None = None
    media_scrape_write_local_nfo: bool | None = None
    media_scrape_download_poster: bool | None = None
    media_scrape_wait_seconds: int | None = None
    media_local_meta_dir: str | None = None
    media_op_delay_min: float | None = None
    media_op_delay_max: float | None = None
    media_organize_schedule_enabled: bool | None = None
    media_scrape_schedule_enabled: bool | None = None
    media_organize_schedule_type: str | None = None
    media_organize_schedule_hours: int | None = None
    media_organize_fixed_times: list[str] | str | None = None


class SelectTaskIn(BaseModel):
    cand_idx: int | None = None
    tmdb_id: int | None = None
    kind: str = "movie"


class BatchStageIn(BaseModel):
    mode: str = "auto_top1"  # auto_top1 | ignore_all
    min_score: float = 0.0


class CommitIn(BaseModel):
    task_ids: list[int] | None = None
    tg_batch_id: str = ""


class UndoIn(BaseModel):
    record_id: int = 0
    batch_id: str = ""


# ---------- 状态概览 ----------

@router.get("/status")
def media_status():
    rows = db.query("SELECT status, count(*) c FROM media_tasks GROUP BY status")
    counts = {r["status"]: int(r["c"]) for r in rows}
    org_rules = db.query("SELECT count(*) c FROM media_rules WHERE enabled=1 AND organize_enabled=1", one=True)["c"]
    scr_rules = db.query("SELECT count(*) c FROM media_rules WHERE enabled=1 AND scrape_enabled=1", one=True)["c"]
    rec_cnt = db.query("SELECT count(*) c FROM organized_records WHERE undone=0", one=True)["c"]
    return {
        "running": media_worker.is_system_running(),
        "tmdb_configured": bool(str(db.get_setting("tmdb_api_key", "") or "").strip()),
        "gate": transfer_gate.get_exclusive_status(),
        "counts": {
            "need_confirm": counts.get("need_confirm", 0),
            "staged": counts.get("staged", 0),
            "queued": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "done": counts.get("done", 0),
            "skipped": counts.get("skipped", 0),
            "failed": counts.get("failed", 0),
            "ignored": counts.get("ignored", 0),
            "organize_rules": int(org_rules or 0),
            "scrape_rules": int(scr_rules or 0),
            "records": int(rec_cnt or 0),
        },
        "last_run": {
            "organize": float(db.get_setting("media_organize_last_run", 0) or 0),
            "scrape": float(db.get_setting("media_scrape_last_run", 0) or 0),
        },
    }


# ---------- 规则管理 ----------

@router.get("/rules")
def list_rules():
    return [dict(r) for r in db.query("SELECT * FROM media_rules ORDER BY id ASC")]


@router.post("/rules")
def create_rule(body: RuleIn):
    src = "/" + (body.source_dir or "").strip().strip("/")
    if src == "/":
        raise HTTPException(400, "源监控目录不能为根目录 `/` 或空")
    tgt = ("/" + body.target_dir.strip().strip("/")) if (body.target_dir or "").strip() else src
    mt = body.media_type if body.media_type in ("auto", "movie", "tv") else "auto"
    rid = db.insert(
        "INSERT INTO media_rules(name,source_dir,target_dir,media_type,organize_enabled,scrape_enabled,enabled,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            (body.name or "").strip() or src.rsplit("/", 1)[-1],
            src, tgt, mt,
            1 if body.organize_enabled else 0,
            1 if body.scrape_enabled else 0,
            1 if body.enabled else 0,
            time.time(),
        ),
    )
    return {"ok": True, "id": rid}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleIn):
    existing = db.query("SELECT id FROM media_rules WHERE id=?", (rule_id,), one=True)
    if not existing:
        raise HTTPException(404, "规则不存在")
    src = "/" + (body.source_dir or "").strip().strip("/")
    if src == "/":
        raise HTTPException(400, "源监控目录不能为根目录 `/` 或空")
    tgt = ("/" + body.target_dir.strip().strip("/")) if (body.target_dir or "").strip() else src
    mt = body.media_type if body.media_type in ("auto", "movie", "tv") else "auto"
    db.execute(
        "UPDATE media_rules SET name=?, source_dir=?, target_dir=?, media_type=?, "
        "organize_enabled=?, scrape_enabled=?, enabled=? WHERE id=?",
        (
            (body.name or "").strip() or src.rsplit("/", 1)[-1],
            src, tgt, mt,
            1 if body.organize_enabled else 0,
            1 if body.scrape_enabled else 0,
            1 if body.enabled else 0,
            rule_id,
        ),
    )
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int):
    db.execute("DELETE FROM media_rules WHERE id=?", (rule_id,))
    return {"ok": True}


# ---------- 整理与刮削独立配置 ----------

@router.get("/settings")
def get_settings():
    s = media_worker.get_media_settings()
    s["tmdb_configured"] = bool(str(s.pop("tmdb_api_key", "") or "").strip())
    s["next_runs"] = media_worker.compute_media_organize_next_runs()
    return s


@router.put("/settings")
def save_settings(body: MediaSettingsIn):
    data = body.model_dump(exclude_none=True)
    if "media_conflict_policy" in data and data["media_conflict_policy"] not in ("skip", "replace_if_better", "keep_both"):
        raise HTTPException(400, "非法冲突处理策略")
    for tpl_key, tpl_kind in (("media_organize_template_movie", "movie"), ("media_organize_template_tv", "tv")):
        if tpl_key in data:
            try:
                data[tpl_key] = validate_organize_template(data[tpl_key], kind=tpl_kind)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
    if "media_extra_min_mb" in data:
        data["media_extra_min_mb"] = max(0, min(2048, int(data["media_extra_min_mb"])))
    if "media_scrape_confidence_threshold" in data:
        data["media_scrape_confidence_threshold"] = max(50, min(99, int(data["media_scrape_confidence_threshold"])))
    if "media_scrape_margin_threshold" in data:
        data["media_scrape_margin_threshold"] = max(0, min(40, int(data["media_scrape_margin_threshold"])))
    if "media_scrape_wait_seconds" in data:
        data["media_scrape_wait_seconds"] = max(0, min(600, int(data["media_scrape_wait_seconds"])))
    if "media_organize_schedule_type" in data:
        st = str(data["media_organize_schedule_type"] or "").strip()
        data["media_organize_schedule_type"] = st if st in ("interval", "fixed_times") else "interval"
    if "media_organize_schedule_hours" in data:
        data["media_organize_schedule_hours"] = max(1, min(168, int(data["media_organize_schedule_hours"])))
    if "media_organize_fixed_times" in data:
        ft = data["media_organize_fixed_times"]
        if isinstance(ft, str):
            try:
                ft = json.loads(ft)
            except Exception:
                ft = []
        if isinstance(ft, list):
            clean = []
            for item in ft:
                m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", str(item).strip())
                if m:
                    clean.append(f"{int(m.group(1)):02d}:{int(m.group(2)):02d}")
            data["media_organize_fixed_times"] = sorted(set(clean))
    if "media_op_delay_min" in data:
        data["media_op_delay_min"] = max(0.3, min(30.0, float(data["media_op_delay_min"])))
    if "media_op_delay_max" in data:
        dmin = float(data.get("media_op_delay_min", db.get_setting("media_op_delay_min", 0.8)) or 0.8)
        data["media_op_delay_max"] = max(dmin, min(60.0, float(data["media_op_delay_max"])))
    if "media_local_meta_dir" in data:
        raw_dir = str(data["media_local_meta_dir"] or "").strip()
        if "\x00" in raw_dir:
            raise HTTPException(400, "元数据目录包含非法空字符")
        if raw_dir:
            resolved_dir = Path(raw_dir).expanduser().resolve()
            if (
                len(resolved_dir.parts) <= 1
                or resolved_dir.is_relative_to(_APP_BACKEND_DIR)
                or _APP_BACKEND_DIR.is_relative_to(resolved_dir)
            ):
                raise HTTPException(400, "本地元数据目录不允许设为系统根目录或程序目录")
            resolved_dir.mkdir(parents=True, exist_ok=True)
            data["media_local_meta_dir"] = str(resolved_dir)
        else:
            data["media_local_meta_dir"] = ""

    for k, v in data.items():
        db.set_setting(k, v)
    return {"ok": True, "next_runs": media_worker.compute_media_organize_next_runs()}


# ---------- 模块 A：目录整理与 Dry-Run ----------

@router.post("/organize/preview")
async def organize_preview(body: RuleIdsIn | None = None):
    return await media_worker.preview_organize(body.rule_ids if body else None)


@router.post("/organize/run")
async def organize_run(body: RuleIdsIn | None = None):
    if not media_worker.is_system_running():
        raise HTTPException(400, "系统顶部运行开关已停止，请先启动系统后再运行目录整理")
    if transfer_gate.is_exclusive_busy():
        mode = transfer_gate.get_active_mode()
        raise HTTPException(409, f"当前有互斥任务（{mode}）正在排队或执行中，请勿同时启动！")
    rules_cnt = db.query("SELECT count(*) c FROM media_rules WHERE enabled=1 AND organize_enabled=1", one=True)["c"]
    if not rules_cnt:
        raise HTTPException(400, "请先在「目录规则」中新增并启用至少一条整理规则")

    rids = body.rule_ids if body else None
    t = media_worker._ACTIVE_TASK_REF.get("organize")
    if t and not t.done():
        raise HTTPException(409, "目录整理已在执行队列中")
    media_worker._ACTIVE_TASK_REF["organize"] = asyncio.create_task(
        media_worker.execute_organize_queue(rule_ids=rids, trigger="manual")
    )
    return {"ok": True, "msg": "已发起目录整理：正在停止接收新转存并消化现有转存队列后按序整理"}


# ---------- 模块 B：媒体刮削 ----------

@router.post("/scrape/run")
async def scrape_run(body: RuleIdsIn | None = None):
    if not media_worker.is_system_running():
        raise HTTPException(400, "系统顶部运行开关已停止，请先启动系统后再运行媒体刮削")
    if not str(db.get_setting("tmdb_api_key", "") or "").strip():
        raise HTTPException(400, "请先在「系统设置」配置 TMDB API Key 再运行媒体刮削")
    if transfer_gate.is_exclusive_busy():
        mode = transfer_gate.get_active_mode()
        raise HTTPException(409, f"当前有互斥任务（{mode}）正在排队或执行中，请勿同时启动！")
    rules_cnt = db.query("SELECT count(*) c FROM media_rules WHERE enabled=1 AND scrape_enabled=1", one=True)["c"]
    if not rules_cnt:
        raise HTTPException(400, "请先在「目录规则」中新增并启用至少一条刮削规则")

    rids = body.rule_ids if body else None
    t = media_worker._ACTIVE_TASK_REF.get("scrape")
    if t and not t.done():
        raise HTTPException(409, "媒体刮削已在执行队列中")
    media_worker._ACTIVE_TASK_REF["scrape"] = asyncio.create_task(
        media_worker.execute_scrape_queue(mode="scan_and_run", rule_ids=rids)
    )
    return {"ok": True, "msg": "已发起媒体刮削：正在消化转存队列后按序匹配（模糊项 0 秒挂起待确认）"}


@router.post("/stop")
def stop_active_media():
    if not transfer_gate.is_exclusive_busy():
        return {"ok": True, "msg": "当前无正在执行的整理或刮削任务"}
    transfer_gate.request_cancel()
    return {"ok": True, "msg": "已发送安全中断信号：将在当前单个文件处理完成后立即停下并恢复主程序"}


# ---------- 待确认列表、暂存、统一提交与手动搜索 ----------

@router.get("/tasks")
def list_tasks(status: str = "all", task_kind: str = "all", limit: int = 120):
    where = ["1=1"]
    params: list = []
    if task_kind in ("scrape", "organize"):
        where.append("task_kind=?")
        params.append(task_kind)
    if status == "pending_group":
        where.append("status IN ('need_confirm','staged','queued','running')")
    elif status != "all" and status:
        where.append("status=?")
        params.append(status)

    lim = max(1, min(500, int(limit or 120)))
    sql = f"SELECT * FROM media_tasks WHERE {' AND '.join(where)} ORDER BY CASE status WHEN 'staged' THEN 0 WHEN 'need_confirm' THEN 1 WHEN 'queued' THEN 2 WHEN 'running' THEN 3 ELSE 4 END, id DESC LIMIT ?"
    params.append(lim)
    rows = db.query(sql, tuple(params))
    out = []
    for row in rows:
        d = dict(row)
        try:
            d["parsed"] = json.loads(d.pop("parsed_json", "{}") or "{}")
        except Exception:
            d["parsed"] = {}
        try:
            d["candidates"] = json.loads(d.pop("candidates_json", "[]") or "[]")
        except Exception:
            d["candidates"] = []
        out.append(d)
    return out


@router.post("/tasks/{task_id}/select")
async def select_task_candidate(task_id: int, body: SelectTaskIn):
    if body.cand_idx is not None:
        res = await asyncio.to_thread(media_worker.stage_task_selection, task_id, body.cand_idx, None, "web")
    elif body.tmdb_id:
        api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
        if not api_key:
            raise HTTPException(400, "未配置 TMDB API Key")
        kind = "tv" if body.kind == "tv" else "movie"
        det = await asyncio.to_thread(tmdb_scraper.get_tmdb_detail, kind, int(body.tmdb_id), api_key)
        if not det:
            raise HTTPException(404, f"TMDB 未查询到 {kind}/{body.tmdb_id}")
        title = str(det.get("name") or det.get("title") or "").strip()
        dt = str(det.get("first_air_date") or det.get("release_date") or "").strip()
        manual_cand = {
            "tmdb_id": int(det["id"]),
            "kind": kind,
            "title": title,
            "year": dt[:4] if len(dt) >= 4 else "",
            "poster_path": str(det.get("poster_path") or ""),
        }
        res = await asyncio.to_thread(media_worker.stage_task_selection, task_id, None, manual_cand, "web")
    else:
        raise HTTPException(400, "请指定候选序号或 TMDB ID")

    if not res["ok"]:
        raise HTTPException(409 if res.get("code") == "locked_status" else 400, res["msg"])
    return res


@router.post("/tasks/{task_id}/unstage")
async def unstage_task(task_id: int):
    res = await asyncio.to_thread(media_worker.unstage_task_selection, task_id, "web")
    if not res["ok"]:
        raise HTTPException(409, res["msg"])
    return res


@router.post("/tasks/{task_id}/ignore")
async def ignore_task(task_id: int):
    res = await asyncio.to_thread(media_worker.ignore_task, task_id, "web")
    if not res["ok"]:
        raise HTTPException(409, res["msg"])
    return res


@router.post("/tasks/batch-stage")
async def batch_stage(body: BatchStageIn):
    rows = [dict(r) for r in await asyncio.to_thread(
        db.query, "SELECT * FROM media_tasks WHERE status='need_confirm' AND task_kind='scrape' ORDER BY id ASC"
    )]
    cnt = 0
    for r in rows:
        if body.mode == "ignore_all":
            res = await asyncio.to_thread(media_worker.ignore_task, int(r["id"]), "web")
            if res["ok"]:
                cnt += 1
        else:
            cands = json.loads(r.get("candidates_json") or "[]")
            if cands and float(cands[0].get("score") or 0) >= float(body.min_score or 0):
                res = await asyncio.to_thread(media_worker.stage_task_selection, int(r["id"]), 0, None, "web")
                if res["ok"]:
                    cnt += 1
    return {"ok": True, "count": cnt, "msg": f"已批量操作 {cnt} 条（未动 115，点「提交队列」后统一生效）"}


@router.post("/tasks/commit")
async def commit_tasks(body: CommitIn | None = None):
    res = await asyncio.to_thread(
        media_worker.commit_staged_tasks,
        body.task_ids if body else None,
        body.tg_batch_id if body else "",
        "web",
    )
    if not res["ok"]:
        code = 409 if res.get("code") == "mutex_busy" else 400
        raise HTTPException(code, res["msg"])
    media_worker.schedule_commit_runner_if_idle()
    return res


@router.delete("/tasks/clear")
def clear_finished_tasks(scope: str = "finished"):
    if scope == "all_history":
        n = db.execute("DELETE FROM media_tasks WHERE status IN ('done','skipped','failed','ignored')")
    else:
        n = db.execute("DELETE FROM media_tasks WHERE status IN ('done','skipped','ignored')")
    return {"ok": True, "deleted": n}


@router.get("/tmdb/search")
async def search_tmdb_manual(q: str, kind: str = "multi", year: str = ""):
    kw = (q or "").strip()
    if not kw:
        return []
    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        raise HTTPException(400, "未配置 TMDB API Key")
    k = kind if kind in ("multi", "tv", "movie") else "multi"
    params = {"api_key": api_key, "query": kw, "language": "zh-CN", "include_adult": "false", "page": 1}
    if year and k in ("tv", "movie"):
        params["year" if k == "movie" else "first_air_date_year"] = year.strip()
    try:
        resp = await asyncio.to_thread(tmdb_scraper.tmdb_get, f"/search/{k}", params)
    except Exception as e:
        # 异常文本可能内嵌带 api_key 的完整 URL，不回显给前端；详情只进服务端日志
        logstream.warn("scrape", f"TMDB 搜索网络异常（{kw[:40]}）: {logstream.exc(e)}")
        raise HTTPException(502, "TMDB 网络请求失败，请稍后重试或检查网络/代理") from e
    if resp.status_code != 200:
        raise HTTPException(502, f"TMDB 查询失败: HTTP {resp.status_code}")
    results = resp.json().get("results") or []
    out = []
    for item in results[:12]:
        mt = item.get("media_type") or k
        if mt not in ("tv", "movie"):
            continue
        dt = str(item.get("first_air_date") or item.get("release_date") or "")
        out.append({
            "tmdb_id": int(item["id"]),
            "kind": mt,
            "title": str(item.get("name") or item.get("title") or "").strip(),
            "original_title": str(item.get("original_name") or item.get("original_title") or "").strip(),
            "year": dt[:4] if len(dt) >= 4 else "",
            "poster_path": str(item.get("poster_path") or ""),
            "overview": str(item.get("overview") or "")[:180],
            "rating": round(float(item.get("vote_average") or 0), 1),
        })
    return out


# ---------- 操作账本、撤销与本地元数据浏览 ----------

@router.get("/records")
def list_records(batch_id: str = "", undone: str = "all", limit: int = 120):
    # 整理刮削账本只展示本模块的记录：定时文件整理（auto_move）的移动账本
    # 在「转存归档 → 整理记录」页展示，两套账本互不混排
    where = ["(action IS NULL OR action != 'auto_move')"]
    params: list = []
    if batch_id:
        where.append("batch_id=?")
        params.append(batch_id)
    if undone in ("0", "1"):
        where.append("undone=?")
        params.append(int(undone))
    lim = max(1, min(500, int(limit or 120)))
    params.append(lim)
    return [dict(r) for r in db.query(f"SELECT * FROM organized_records WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", tuple(params))]


@router.post("/records/undo")
async def undo_records(body: UndoIn):
    res = await media_worker.undo_organized(batch_id=body.batch_id, record_id=body.record_id)
    if not res["ok"]:
        raise HTTPException(400, res["msg"])
    return res


def _resolve_task_meta_dir(task_id: int) -> Path:
    row = db.query("SELECT local_meta_dir FROM media_tasks WHERE id=?", (int(task_id),), one=True)
    if not row or not row["local_meta_dir"]:
        raise HTTPException(404, "该任务尚无本地生成的元数据目录")
    custom_root = str(db.get_setting("media_local_meta_dir", "") or "").strip()
    base = (Path(custom_root).expanduser() if custom_root else config.MEDIA_META_DIR).resolve()
    target = Path(row["local_meta_dir"]).resolve()
    if target == base or not target.is_relative_to(base):
        raise HTTPException(403, "元数据路径超出允许根目录")
    if not target.exists():
        raise HTTPException(404, "本地元数据目录不存在")
    return target


@router.get("/meta/inspect")
def inspect_local_meta(task_id: int):
    target = _resolve_task_meta_dir(task_id)
    nfos = []
    for p in sorted(target.rglob("*.nfo")):
        try:
            p_res = p.resolve()
            if not p_res.is_relative_to(target) or not p_res.is_file():
                continue
            rel = str(p.relative_to(target)).replace("\\", "/")
            nfos.append({"path": rel, "content": p_res.read_text(encoding="utf-8", errors="ignore")[:24000]})
        except Exception:
            pass
    poster_file = (target / "poster.jpg").resolve()
    fanart_file = (target / "fanart.jpg").resolve()
    return {
        "meta_dir": str(target),
        "has_poster": poster_file.is_relative_to(target) and poster_file.is_file(),
        "has_fanart": fanart_file.is_relative_to(target) and fanart_file.is_file(),
        "nfos": nfos[:30],
    }


@poster_router.get("/poster/local")
def get_local_poster(
    task_id: int,
    file: str = "poster.jpg",
    _=Depends(_require_poster_or_user),
):
    if file not in ("poster.jpg", "fanart.jpg"):
        raise HTTPException(400, "非法图片文件名")
    meta_dir = _resolve_task_meta_dir(task_id)
    target = (meta_dir / file).resolve()
    if not target.is_relative_to(meta_dir) or not target.is_file():
        raise HTTPException(404, "本地图片不存在")
    return Response(
        content=target.read_bytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )
