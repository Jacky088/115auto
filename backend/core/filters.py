"""黑名单过滤：频道 / 关键词 / 文件名正则 / 体积下限，命中写 blacklist_hits。"""
import re
import time

from .. import database as db

_cache: tuple[dict, float] = ({}, 0.0)
_CACHE_TTL = 5.0


def _blacklist() -> dict:
    global _cache
    now = time.time()
    if now - _cache[1] > _CACHE_TTL:
        rows = db.query("SELECT * FROM blacklist")
        data: dict[str, list] = {"channel": [], "keyword": [], "file_pattern": []}
        for r in rows:
            if r["type"] in data:
                data[r["type"]].append(r["value"])
        _cache = (data, now)
    return _cache[0]


def invalidate():
    """黑名单变更后立即失效缓存（否则最多 5 秒内仍用旧名单）。"""
    global _cache
    _cache = ({}, 0.0)


def check_message(text: str, channel: str | None = None) -> tuple[bool, str, str]:
    """消息级检查：频道黑名单 + 标题关键词。返回 (blocked, rule_type, rule_value)。"""
    if channel:
        for ch in _blacklist()["channel"]:
            if ch.lower() in channel.lower():
                return True, "channel", ch
    text = (text or "")[:4000]  # 与 classifier 同口径：截断超长文本再匹配
    for kw in _blacklist()["keyword"]:
        if kw and kw.lower() in text.lower():
            return True, "keyword", kw
    return False, "", ""


def check_files(file_names: list[str], total_size: int) -> tuple[bool, str, str]:
    """分享内容检查：文件名正则 + 体积下限。返回 (blocked, rule_type, rule_value)。"""
    pats = _blacklist()["file_pattern"]
    for name in file_names:
        for pat in pats:
            try:
                if re.search(pat, name, re.IGNORECASE):
                    return True, "file_pattern", pat
            except re.error:
                continue
    try:
        min_size_gb = float(db.get_setting("min_size_gb", 0.2) or 0)
    except (TypeError, ValueError):
        min_size_gb = 0.2
    if min_size_gb > 0 and total_size > 0 and total_size < min_size_gb * (1 << 30):
        return True, "min_size", f"{min_size_gb}GB"
    return False, "", ""


def record_hit(channel, message_id, text, share_code, rule_type, rule_value, stage):
    db.execute(
        "INSERT INTO blacklist_hits(channel,message_id,text,share_code,rule_type,rule_value,stage,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (channel, message_id, (text or "")[:500], share_code or "", rule_type, rule_value, stage, time.time()),
    )
