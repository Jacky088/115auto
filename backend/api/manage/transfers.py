"""转存任务列表、重试、手动入队、批量删除与向前补全路由。"""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException

from ... import database as db
from ...core import logstream, pipeline
from ..deps import require_user
from ._common import _clamp_page, _like_escape

router = APIRouter()


@router.get("/transfers")
def list_transfers(status: str = "", category: str = "", search: str = "",
                   page: int = 1, size: int = 30, _=Depends(require_user)):
    page, size = _clamp_page(page, size, 200)
    where, params = ["1=1"], []
    if status:
        where.append("status=?"); params.append(status)
    if category:
        where.append("category=?"); params.append(category)
    if search:
        search = search.strip()[:100]
        esc = _like_escape(search)
        where.append("(title LIKE ? ESCAPE '\\' OR share_code LIKE ? ESCAPE '\\')")
        params += [f"%{esc}%", f"%{esc}%"]
    w = " AND ".join(where)
    total = db.query(f"SELECT count(*) c FROM transfers WHERE {w}", tuple(params), one=True)["c"]
    rows = db.query(
        f"SELECT * FROM transfers WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params + [size, (page - 1) * size]))
    return {"total": total, "items": [dict(r) for r in rows]}


@router.post("/transfers/{t_id}/retry")
def retry_transfer(t_id: int, _=Depends(require_user)):
    row = db.query("SELECT status, share_code FROM transfers WHERE id=?", (t_id,), one=True)
    if not row:
        raise HTTPException(404, "记录不存在")
    # 成功/已提交下载/正在转存的任务不能重试，否则会重复转存
    if row["status"] in ("success", "submitted", "running"):
        label = {"success": "已成功", "submitted": "已提交云下载", "running": "正在转存"}[row["status"]]
        raise HTTPException(400, f"{label}的任务不能重试")
    # 活动任务唯一：同分享码已有排队/转存中任务时不重复入队（避免 115 生成副本）
    if db.query("SELECT 1 FROM transfers WHERE share_code=? AND status IN ('queued','running','submitted')"
                " AND id!=?", (row["share_code"], t_id), one=True):
        raise HTTPException(400, "该分享已有排队/转存中的任务，无法重复入队")
    db.execute("UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL WHERE id=?", (t_id,))
    logstream.info("transfer", f"手动重试任务 #{t_id}（原状态: {row['status']}）")
    return {"ok": True}


@router.post("/transfers/retry-failed")
def retry_all_failed(_=Depends(require_user)):
    """批量重试全部失败任务。

    活动任务唯一约束下的安全批量：同一分享码只重试最新一条失败记录，
    且跳过已有排队/转存中任务的分享码（避免触发唯一索引冲突）。
    """
    n = db.execute(
        "UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL"
        " WHERE status='failed'"
        " AND id=(SELECT MAX(id) FROM transfers t2"
        "         WHERE t2.share_code=transfers.share_code AND t2.status='failed')"
        " AND share_code NOT IN (SELECT share_code FROM transfers"
        "                         WHERE status IN ('queued','running','submitted'))")
    logstream.info("transfer", f"批量重试 {n} 个失败任务")
    return {"ok": True, "count": n}


@router.post("/transfers/batch-delete")
def batch_delete_transfers(body: dict, _=Depends(require_user)):
    """批量删除任务记录（正在转存/已成功的不可删）。"""
    raw = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "请选择要删除的记录")
    ids = []
    for x in raw[:500]:  # 单次上限，避免超大 SQL
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(400, "记录 id 无效")
    ids = list(dict.fromkeys(ids))
    rows = db.query(
        f"SELECT id, status FROM transfers WHERE id IN ({','.join('?' * len(ids))})",
        tuple(ids))
    deletable = [r["id"] for r in rows if r["status"] not in ("running", "success")]
    skipped = len(ids) - len(deletable)  # 含不存在/正在转存/已成功
    n = 0
    if deletable:
        n = db.execute(
            f"DELETE FROM transfers WHERE id IN ({','.join('?' * len(deletable))})",
            tuple(deletable))
        logstream.info("transfer", f"批量删除 {n} 条任务记录（跳过 {skipped} 条不可删除记录）")
    return {"ok": True, "deleted": n, "skipped": skipped}


@router.post("/transfers/manual")
def manual_transfer(body: dict, _=Depends(require_user)):
    """网页手动输入或 PWA 系统分享链接直接入队转存。"""
    text = str(body.get("text") or body.get("share_url") or "").strip()
    if not text:
        raise HTTPException(400, "请提供转存链接或包含链接的文本")
    force_category = str(body.get("category") or "").strip()
    mid = int(time.time() * 1000) % 1000000000
    try:
        res = pipeline.process_message(channel="web", message_id=mid,
                                       text=text, bypass=True,
                                       force_category=force_category)
    except Exception as e:
        logstream.error("transfer", f"网页手动转存失败: {logstream.exc(e)}")
        raise HTTPException(500, f"处理失败: {str(e)[:120]}")
    return {"ok": True, "result": res}


@router.delete("/transfers/{t_id}")
def cancel_transfer(t_id: int, _=Depends(require_user)):
    row = db.query("SELECT status FROM transfers WHERE id=?", (t_id,), one=True)
    if not row:
        raise HTTPException(404, "记录不存在")
    # 与前端删除按钮保持一致：可删除排队/已提交跟踪/失败/跳过/拦截的记录；
    # 正在转存或已成功的记录不允许删除，避免误删正在使用/已完成的数据。
    if row["status"] in ("running", "success"):
        label = {"running": "正在转存", "success": "已成功"}[row["status"]]
        raise HTTPException(400, f"{label}的任务不能删除")
    db.execute("DELETE FROM transfers WHERE id=?", (t_id,))
    logstream.info("transfer", f"已删除任务记录 #{t_id}（原状态：{row['status']}）")
    return {"ok": True}




# ---------- 向前补全 ----------
@router.post("/backfill/episodes")
async def backfill_episodes(body: dict, _=Depends(require_user)):
    """向前补全：以某条成功转存为锚点，向前翻找频道历史里同剧的早期分集并入队。

    body: {transfer_id}（锚点记录）或 {keyword}（按标题关键词找最近的成功转存）；
          可选 base（剧名关键词，标题无剧名时手动指定）、current_ep（标题无法解析集数时手动指定）。
    补全任务带最高队列优先级（priority=1），额度有限时优先于普通转存消耗。
    """
    from ...tg.episode_backfill import parse_episode, submit_backfill

    keyword = str(body.get("keyword") or "").strip()
    if len(keyword) > 100:
        raise HTTPException(400, "剧名关键词过长（最多 100 字符）")
    try:
        tid = int(body.get("transfer_id"))
    except (TypeError, ValueError):
        tid = 0
    row = None
    if tid:
        row = await asyncio.to_thread(
            db.query,
            "SELECT id, title, channel, message_id, status, kind, category, target_dir"
            " FROM transfers WHERE id=?", (tid,), one=True)
        if not row:
            raise HTTPException(404, "转存记录不存在")
        if row["status"] != "success" or row["kind"] != "share" \
                or not row["channel"] or not row["message_id"]:
            raise HTTPException(400, "只能以「已成功的分享转存记录」作为锚点（需包含来源频道与消息编号）")
    elif keyword:
        row = await asyncio.to_thread(
            db.query,
            "SELECT id, title, channel, message_id, status, kind, category, target_dir FROM transfers"
            " WHERE status='success' AND kind='share' AND message_id>0 AND title LIKE ? ESCAPE '\\'"
            " ORDER BY id DESC LIMIT 1", (f"%{_like_escape(keyword)}%",), one=True)
        if not row:
            raise HTTPException(404, f"没有找到标题包含「{keyword}」的成功转存记录")
    else:
        raise HTTPException(400, "请提供 transfer_id 或 keyword（按标题关键词定位剧集）")

    base = str(body.get("base") or "").strip() or keyword
    try:
        cur_ep = int(body.get("current_ep"))
    except (TypeError, ValueError):
        cur_ep = None
    if cur_ep is not None:
        if cur_ep < 2:
            raise HTTPException(400, "当前集数需大于 1（第 1 集没有更早的分集可补）")
        cur_ep = min(cur_ep, 999)  # 与标题集数解析口径一致，防极端值构造超大列表
    parsed_season, parsed_ep = parse_episode(row["title"] or "")
    if cur_ep is not None:
        ep = cur_ep
        season = parsed_season
    else:
        season, ep = parsed_season, parsed_ep
    try:
        summary = await submit_backfill(row["channel"], row["message_id"], row["title"] or "",
                                        base=base, season=season, ep=ep,
                                        anchor_category=row["category"] or "",
                                        anchor_target=row["target_dir"] or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    logstream.info("system", f"用户发起向前补全：《{summary['base']}》{summary['episodes']}"
                             f"（频道 @{summary['channel']}，任务将优先入队）")
    return {"ok": True, **summary}
