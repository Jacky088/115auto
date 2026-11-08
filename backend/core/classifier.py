"""分类引擎：DB 规则（正则，按 priority 升序）匹配消息标题，首条命中生效。"""
import re

from .. import database as db

_cache: tuple[list[dict], float] = ([], 0.0)
_CACHE_TTL = 5.0

# 频道消息自带的分类标签（如 #电影 / #剧集）优先于正则规则：
# 标签是频道维护者的显式标注，比标题里的年份/画质等特征更可靠。
_TAG_ALIASES = {
    "电影": "电影", "剧集": "电视剧", "电视剧": "电视剧",
    "动漫": "动漫", "动画": "动漫", "番剧": "动漫",
    "综艺": "综艺", "纪录": "纪录片", "纪录片": "纪录片",
    "少儿": "少儿", "儿童": "少儿",
}
_TAG_RE = re.compile(r"#([\u4e00-\u9fa5A-Za-z0-9]{1,10})")
# 行首装饰符号（emoji、竖线、破折号等）
_DECOR_RE = re.compile(r"^[\W_]+")


def headline(text: str, limit: int = 120) -> str:
    """从频道消息中提取展示名称：跳过纯表情/符号行与链接行，取首个有效行。

    频道帖子通常是「📺 / 名称 (年份) / 简中 1080P ...」的多行结构，
    通知应显示名称本身，而不是 115 分享里的文件夹名（如 Season 04）。
    """
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith(("http://", "https://", "magnet:", "ed2k:", "ftp://", "tg://")):
            continue
        s = _DECOR_RE.sub("", s).strip().lstrip("#").strip()
        if not s:
            continue
        return s[:limit]
    return ""


def _tag_category(text: str) -> tuple[str, str]:
    """返回频道标签对应的分类（仅当该分类存在于路由表时生效）。"""
    known = {r["category"] for r in db.query("SELECT category FROM routes")}
    for m in _TAG_RE.finditer(text):
        cat = _TAG_ALIASES.get(m.group(1))
        if cat and cat in known:
            return cat, "#" + m.group(1)
    return "", ""


def _rules() -> list[dict]:
    global _cache
    import time
    now = time.time()
    if now - _cache[1] > _CACHE_TTL:
        rows = db.query("SELECT * FROM rules WHERE enabled=1 ORDER BY priority ASC, id ASC")
        # 空正则 re.search("") 会命中一切——过滤掉，避免整站分类被一条坏规则劫持
        _cache = ([dict(r) for r in rows if (r["pattern"] or "").strip()], now)
    return _cache[0]


def invalidate():
    """规则/路由变更后立即失效缓存（否则最多 5 秒内仍用旧规则）。"""
    global _cache
    _cache = ([], 0.0)


# 单次参与正则匹配的最大文本长度：频道正文（≤4KB）足够，telegra.ph 解析回灌的长文
# 截断后再匹配——分类特征（标签/年份/集数/画质）都在开头，截断不影响结果，
# 同时避免超长输入放大坏规则的回溯耗时
MAX_MATCH_LEN = 4000


def classify(text: str) -> tuple[str, str]:
    """返回 (category, matched_pattern)。未命中返回 ("", "")。

    优先采用频道消息里的显式标签（#电影/#剧集/#动漫…），其次才按规则匹配标题。
    """
    if not text:
        return "", ""
    text = text[:MAX_MATCH_LEN]
    cat, tag = _tag_category(text)
    if cat:
        return cat, tag
    for rule in _rules():
        try:
            if re.search(rule["pattern"], text, re.IGNORECASE):
                return rule["category"], rule["pattern"]
        except re.error:
            continue
    return "", ""


def resolve_target(category: str, kind: str = "share") -> str:
    """分类 → 115 目录。kind: share 分享转存 | offline 云下载。
    云下载优先用 offline_dir，留空回退 target_dir；'skip' 或无路由返回 ''（不处理）。"""
    if not category:
        return ""
    row = db.query("SELECT target_dir, offline_dir FROM routes WHERE category=?", (category,), one=True)
    if not row:
        return ""
    d = row["target_dir"]
    if kind == "offline":
        d = row["offline_dir"] or row["target_dir"]
    return "" if (not d or d == "skip") else d


def test_text(text: str) -> dict:
    """规则测试器：完整模拟一条标题的处理结果（含功能开关状态与分类型目标目录）。"""
    from . import extractor, filters
    category, pattern = classify(text)
    blocked, rule_type, rule_value = filters.check_message(text, channel=None)
    unclassified_action = db.get_setting("unclassified_action", "skip")
    if not category:
        target = "" if unclassified_action == "skip" else str(unclassified_action)
        skipped = unclassified_action == "skip"
    else:
        target = resolve_target(category)
        skipped = target == ""
    # 消息中出现的、当前被功能开关关闭的链接类型
    disabled_kinds = []
    links = extractor.extract(text)
    kinds = {l.get("kind", "share") for l in links}
    if "share" in kinds and not db.get_setting("enable_share_transfer", True):
        disabled_kinds.append("分享转存")
    if "offline" in kinds and not db.get_setting("enable_offline_download", True):
        disabled_kinds.append("云下载")
    # 云下载目标（消息含 ED2K/磁力时显示）
    offline_target = resolve_target(category, kind="offline") if category else ""
    return {
        "text": text,
        "category": category,
        "matched_rule": pattern,
        "blocked": blocked,
        "block_rule_type": rule_type,
        "block_rule_value": rule_value,
        "target_dir": target,
        "target_kind": "offline" if "offline" in kinds else ("share" if kinds else ""),
        "offline_target_dir": offline_target,
        "skipped": skipped,
        "disabled_kinds": disabled_kinds,
    }
