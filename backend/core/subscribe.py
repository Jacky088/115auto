"""订阅追剧：维护关键词订阅清单，只转存命中的内容，并跟踪每部剧的追更进度。

两种用法：
- 全量模式（默认，subscription_only=False）：所有消息照常转存，命中订阅的
  额外记录进度并推送「追剧更新」通知；
- 订阅模式（subscription_only=True）：只转存命中订阅关键词的消息，其余仅记录。
"""
import re
import time
import unicodedata

from .. import database as db

# 归一化时仅保留：数字、小写字母、中日韩文字（汉字/假名/谚文）
_KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _norm(s: str) -> str:
    """匹配归一化：NFKC（全角→半角、连字等）→ 小写 → 去掉空格与全部标点符号。

    「Game of Thrones」「Game.of.Thrones」「game-of-thrones」「GameOfThrones」
    归一化后都是 gameofthrones；「庆余年：第三季」与「庆余年 第三季」同理。
    """
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return _KEEP_RE.sub("", s)


def subscription_only() -> bool:
    return bool(db.get_setting("subscription_only", False))


def match(text: str) -> dict | None:
    """返回第一条命中的启用中订阅，未命中返回 None。

    匹配策略（两级）：
    1. 归一化子串：双方去空格/标点、全角转半角、忽略大小写后做子串匹配——
       兼容发布标题里的各种分隔写法（点/横线/空格/全角符号）；
    2. 短词边界保护：归一化后不足 4 个字符的纯英文短词（如 IT、Up）
       改用「原文完整单词匹配」（左右不能是字母数字），避免误命中
       supernatural 这类包含它的长词。
    """
    if not text:
        return None
    low = str(text).lower()
    text_norm = _norm(text)
    for r in db.query("SELECT * FROM subscriptions WHERE enabled=1 ORDER BY id"):
        kw = str(r["keyword"] or "").strip()
        if not kw:
            continue
        kw_norm = _norm(kw)
        if not kw_norm:
            continue
        if kw_norm in text_norm:
            # 短英文词的误报保护：归一化域太宽，退回原文按完整单词匹配
            if len(kw_norm) < 4 and kw_norm.isascii() and kw_norm.isalpha():
                if not re.search(rf"(?<![0-9a-z]){re.escape(kw.lower())}(?![0-9a-z])", low):
                    continue
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
