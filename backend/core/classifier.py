"""分类引擎：DB 规则（正则，按 priority 升序）匹配消息标题，首条命中生效。"""
import re

from .. import database as db

_cache: tuple[list[dict], float] = ([], 0.0)
_CACHE_TTL = 5.0


def _rules() -> list[dict]:
    global _cache
    import time
    now = time.time()
    if now - _cache[1] > _CACHE_TTL:
        rows = db.query("SELECT * FROM rules WHERE enabled=1 ORDER BY priority ASC, id ASC")
        _cache = ([dict(r) for r in rows], now)
    return _cache[0]


def classify(text: str) -> tuple[str, str]:
    """返回 (category, matched_pattern)。未命中返回 ("", "")。"""
    if not text:
        return "", ""
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
