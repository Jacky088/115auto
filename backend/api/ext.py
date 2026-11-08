"""对外 API：供 Home Assistant / 脚本等第三方集成的轻量只读+入队接口。

鉴权：请求头 X-API-Token 与设置页生成的 api_token 比对（常数时间比较）；
token 留空 = 对外 API 关闭。附带简易限速，防第三方脚本失控打爆本机。
路由前缀 /api/ext/v1，与网页登录体系（JWT）完全隔离。
"""
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import database as db
from ..core import logstream
from ..worker.queue_worker import validate_link

router = APIRouter(prefix="/api/ext/v1")

# 对外 API 限速：60 秒窗口内最多 120 次（HA 数秒级轮询绰绰有余，失控脚本则被拦下）
_rl_lock = threading.Lock()
_rl_records: list[float] = []
_RATE_LIMIT = 120
_RATE_WINDOW = 60.0


def _rate_limited() -> bool:
    now = time.time()
    with _rl_lock:
        while _rl_records and now - _rl_records[0] > _RATE_WINDOW:
            _rl_records.pop(0)
        if len(_rl_records) >= _RATE_LIMIT:
            return True
        _rl_records.append(now)
        return False


def require_token(x_api_token: str = Header(default="")):
    # 限速在鉴权前计数：失败尝试同样受限，防爆破
    if _rate_limited():
        raise HTTPException(429, f"请求过于频繁（{_RATE_LIMIT} 次/分钟），请降低调用频率")
    token = str(db.get_setting("api_token", "") or "").strip()
    if not token:
        raise HTTPException(403, "对外 API 未启用（请在设置页生成 API Token 后开启）")
    try:
        # encode 比较：非 ASCII 的畸形头不会抛 TypeError
        ok = secrets.compare_digest(x_api_token.encode("utf-8"), token.encode("utf-8"))
    except Exception:
        ok = False
    if not x_api_token or not ok:
        raise HTTPException(401, "API Token 无效")


def _share_code_from_url(url: str) -> tuple[str, str]:
    """从 115 分享链接提取 (share_code, receive_code)。"""
    m = re.search(r"115\.com/s/([0-9A-Za-z]+)(?:\?password=([0-9A-Za-z]+))?", url or "")
    if m:
        return m.group(1), m.group(2) or ""
    return "", ""


@router.get("/status", dependencies=[Depends(require_token)])
def status():
    """运行状态摘要（队列/今日额度/空间）。"""
    from datetime import datetime

    queued = db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
    day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    today_s = db.query("SELECT count(*) c FROM transfers WHERE status='success' AND created_at>=?",
                       (day_start,), one=True)["c"]
    space = None
    try:
        from ..pan115 import client as pan
        sp = pan.space_info()
        space = {"used": sp.get("use", 0), "total": sp.get("total", 0),
                 "free": sp.get("free", 0), "ratio": sp.get("ratio", 0)}
    except Exception:
        pass
    return {"running": bool(db.get_setting("running", False)),
            "paused": bool(db.get_setting("paused", False)),
            "queue": queued,
            "today_success": today_s,
            "quota_today": db.quota_today(),
            "daily_limit": db.get_setting("daily_limit", 50),
            "space": space,
            "time": time.time()}


@router.get("/transfers/recent", dependencies=[Depends(require_token)])
def recent_transfers(limit: int = 20):
    """最近转存记录（默认 20 条，最多 100）。"""
    limit = max(1, min(limit, 100))
    rows = db.query("SELECT id,kind,share_code,share_url,title,category,target_dir,size,file_count,"
                    "status,error,channel,created_at,finished_at FROM transfers ORDER BY id DESC LIMIT ?",
                    (limit,))
    return [dict(r) for r in rows]


@router.post("/transfers", dependencies=[Depends(require_token)])
def enqueue_transfer(body: dict):
    """手动入队一个分享转存任务。

    body: {"share_url": "https://115.com/s/xxx?password=yyy"} 或
          {"share_code": "xxx", "receive_code": "yyy"}，可选 "title"。
    返回 {queued, duplicate, id}。重复链接按 duplicate 留痕不重复转存。
    """
    url = str(body.get("share_url") or "").strip()
    code = str(body.get("share_code") or "").strip()
    rc = str(body.get("receive_code") or "").strip()
    if url and not code:
        code, rc = _share_code_from_url(url)
    if not rc and "?" in url:
        m = re.search(r"[?&]password=([0-9A-Za-z]+)", url)
        if m:
            rc = m.group(1)
    share_url = url or f"https://115.com/s/{code}" + (f"?password={rc}" if rc else "")
    err = validate_link("share", code, share_url)
    if err:
        raise HTTPException(400, err)
    title = str(body.get("title") or "")[:200]
    row = db.query("SELECT id, status FROM transfers WHERE share_code=?"
                   " AND status IN ('queued','running','submitted','success') ORDER BY id LIMIT 1",
                   (code,), one=True)
    if row:
        return {"queued": False, "duplicate": True, "id": row["id"],
                "reason": f"已存在记录 #{row['id']}（{row['status']}）"}
    db.execute(
        "INSERT INTO transfers(kind,share_code,receive_code,share_url,title,category,target_dir,"
        "status,bypass_filters,created_at) VALUES('share',?,?,?,?,'','','queued',0,?)",
        (code, rc, share_url, title, time.time()))
    row = db.query("SELECT id FROM transfers WHERE share_code=? AND status='queued'"
                   " ORDER BY id DESC LIMIT 1", (code,), one=True)
    logstream.info("transfer", f"对外 API 入队分享转存: {code}" + (f"（{title[:40]}）" if title else ""))
    return {"queued": True, "duplicate": False, "id": row["id"] if row else None}
