"""重复文件清理 REST API（清理规则 CRUD、Dry-Run 预览、确认执行、批次账本）。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import database as db
from ..core import transfer_gate
from ..worker import dedupe_worker, scan_index
from .manage import require_user

router = APIRouter(prefix="/api/media/dedupe", dependencies=[Depends(require_user)])


class DedupeRuleIn(BaseModel):
    name: str = ""
    source_dir: str
    include_subdirs: bool = True
    min_size_mb: int = 0
    enabled: bool = True


class DedupePreviewIn(BaseModel):
    rule_ids: list[int] | None = None
    confirm_large: bool = False  # 预估目录超过 2 万时，前端确认后置 true 再发起


class DedupeDeleteItem(BaseModel):
    file_id: str
    sha1: str
    size: int = 0


class DedupeRunIn(BaseModel):
    rule_id: int
    delete_items: list[DedupeDeleteItem]


def _norm_source(raw: str) -> str:
    src = "/" + (raw or "").strip().strip("/")
    if src == "/":
        raise HTTPException(400, "清理源目录不能为根目录 `/` 或空")
    return src


def _norm_min_mb(raw: int) -> int:
    return max(0, min(1024 * 1024, int(raw or 0)))


# ---------- 清理规则 ----------

@router.get("/rules")
def list_rules():
    return [dict(r) for r in db.query("SELECT * FROM dedupe_rules ORDER BY id ASC")]


@router.post("/rules")
def create_rule(body: DedupeRuleIn):
    src = _norm_source(body.source_dir)
    rid = db.insert(
        "INSERT INTO dedupe_rules(name,source_dir,include_subdirs,min_size_mb,enabled,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (
            (body.name or "").strip() or src.rsplit("/", 1)[-1],
            src,
            1 if body.include_subdirs else 0,
            _norm_min_mb(body.min_size_mb),
            1 if body.enabled else 0,
            time.time(),
        ),
    )
    return {"ok": True, "id": rid}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: DedupeRuleIn):
    if not db.query("SELECT id FROM dedupe_rules WHERE id=?", (rule_id,), one=True):
        raise HTTPException(404, "清理规则不存在")
    src = _norm_source(body.source_dir)
    db.execute(
        "UPDATE dedupe_rules SET name=?, source_dir=?, include_subdirs=?, min_size_mb=?, enabled=? WHERE id=?",
        (
            (body.name or "").strip() or src.rsplit("/", 1)[-1],
            src,
            1 if body.include_subdirs else 0,
            _norm_min_mb(body.min_size_mb),
            1 if body.enabled else 0,
            rule_id,
        ),
    )
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int):
    db.execute("DELETE FROM dedupe_rules WHERE id=?", (rule_id,))
    return {"ok": True}


# ---------- 预览与执行 ----------

@router.post("/preview")
async def preview(body: DedupePreviewIn | None = None):
    return await dedupe_worker.preview_dedupe(body.rule_ids if body else None)


@router.post("/preview/start")
async def preview_start(body: DedupePreviewIn | None = None):
    """异步扫描预览：立即返回 job_id，前端轮询 /preview/status 显示进度。"""
    rids = body.rule_ids if body else None
    confirm_large = bool(body.confirm_large) if body else False
    sql = "SELECT source_dir FROM dedupe_rules WHERE enabled=1"
    params: list = []
    if rids:
        sql += " AND id IN (" + ",".join("?" for _ in rids) + ")"
        params.extend(int(x) for x in rids)
    paths = [r["source_dir"] for r in db.query(sql, tuple(params))]
    guard = scan_index.scope_guard(paths, confirm_large)
    if guard:
        return guard
    res = scan_index.start_job(
        "dedupe_preview", lambda ctx: dedupe_worker.preview_dedupe(rids, ctx))
    if not res.get("ok"):
        raise HTTPException(409, res.get("msg") or "扫描任务发起失败")
    return res


@router.get("/preview/status")
async def preview_status(job_id: str):
    return scan_index.job_status(job_id)


@router.post("/preview/cancel")
async def preview_cancel(body: dict | None = None):
    return scan_index.cancel_job(str((body or {}).get("job_id") or ""))


@router.post("/run")
async def run(body: DedupeRunIn):
    if not dedupe_worker.is_system_running():
        raise HTTPException(400, "系统顶部运行开关已停止，请先启动系统后再执行重复清理")
    if transfer_gate.is_exclusive_busy():
        mode = transfer_gate.get_active_mode()
        raise HTTPException(409, f"当前有互斥任务（{mode}）正在排队或执行中，请勿同时启动！")
    rule = db.query("SELECT * FROM dedupe_rules WHERE id=? AND enabled=1", (int(body.rule_id),), one=True)
    if not rule:
        raise HTTPException(400, "清理规则不存在或已停用，请刷新后重试")
    if not body.delete_items:
        raise HTTPException(400, "请至少选择一个需要移入回收站的重复文件")
    res = dedupe_worker.start_dedupe(int(body.rule_id), [x.model_dump() for x in body.delete_items])
    if not res.get("ok"):
        raise HTTPException(409, res.get("msg") or "重复清理发起失败")
    return res


# ---------- 清理账本 ----------

@router.get("/records")
def list_records(batch_id: str = "", limit: int = 300):
    where = ["1=1"]
    params: list = []
    if batch_id:
        where.append("batch_id=?")
        params.append(batch_id)
    lim = max(1, min(1000, int(limit or 300)))
    params.append(lim)
    records = [
        dict(r) for r in db.query(
            f"SELECT * FROM dedupe_records WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
    ]
    batches = [
        dict(r) for r in db.query(
            "SELECT batch_id, rule_id, COUNT(*) AS count, "
            "SUM(CASE WHEN status='deleted' THEN 1 ELSE 0 END) AS deleted, "
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
            "SUM(size) AS bytes, MAX(created_at) AS created_at "
            "FROM dedupe_records GROUP BY batch_id, rule_id ORDER BY MAX(id) DESC LIMIT 50"
        )
    ]
    return {"batches": batches, "records": records}
