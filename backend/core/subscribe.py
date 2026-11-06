"""订阅追剧：维护关键词订阅清单，只转存命中的内容，并跟踪每部剧的追更进度。

两种用法：
- 全量模式（默认，subscription_only=False）：所有消息照常转存，命中订阅的
  额外记录进度并推送「追剧更新」通知；
- 订阅模式（subscription_only=True）：只转存命中订阅关键词的消息，其余仅记录。
"""
import time

from .. import database as db


def subscription_only() -> bool:
    return bool(db.get_setting("subscription_only", False))


def match(text: str) -> dict | None:
    """返回第一条命中的启用中订阅（关键词不区分大小写子串匹配），未命中返回 None。"""
    if not text:
        return None
    low = text.lower()
    for r in db.query("SELECT * FROM subscriptions WHERE enabled=1 ORDER BY id"):
        kw = str(r["keyword"] or "").strip()
        if kw and kw.lower() in low:
            return dict(r)
    return None


def record_progress(sub: dict, title: str, ep: str) -> bool:
    """记录命中进度，返回是否为新的一集（可发追更通知）。

    同一集重复命中（不同频道的同剧消息）不重复通知；无集数标识的首次命中也记录。
    """
    old_ep = sub.get("last_ep") or ""
    if ep and ep == old_ep:
        return False
    db.execute(
        "UPDATE subscriptions SET last_ep=?, last_title=?, last_match_at=? WHERE id=?",
        (ep or old_ep, (title or "")[:200], time.time(), sub["id"]))
    return bool(ep and ep != old_ep) or not old_ep
