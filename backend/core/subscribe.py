"""订阅追剧：维护关键词订阅清单，只转存命中的内容，并跟踪每部剧的追更进度。

两种用法：
- 全量模式（默认，subscription_only=False）：所有消息照常转存，命中订阅的
  额外记录进度并推送「追剧更新」通知；
- 订阅模式（subscription_only=True）：只转存命中订阅关键词的消息，其余仅记录。

订阅支持「关键词 + 别名」：从 TMDB 订阅时会同时保存本地化名称与原始名称，
频道帖子用任一名称都能命中；追更进度按 (季, 集) 数值比较，不依赖标题写法。
"""
import json
import re
import time
import unicodedata

from .. import database as db
from . import episode

# 归一化时仅保留：数字、小写字母、中日韩文字（汉字/假名/谚文）
_KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

MAX_KEYWORD_LEN = 100
MAX_ALIASES = 20


def normalize(s: str) -> str:
    """匹配归一化：NFKC（全角→半角、连字等）→ 小写 → 去掉空格与全部标点符号。

    「Game of Thrones」「Game.of.Thrones」「game-of-thrones」「GameOfThrones」
    归一化后都是 gameofthrones；「庆余年：第三季」与「庆余年 第三季」同理。
    """
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return _KEEP_RE.sub("", s)


def subscription_only() -> bool:
    """全局兜底开关：频道不存在（如补漏抓取）时才使用，正常以频道级 sub_only 为准。"""
    return bool(db.get_setting("subscription_only", False))


def aliases_of(sub: dict) -> list[str]:
    """读取订阅别名（兼容 JSON 数组、逗号/换行分隔与旧库空值）。"""
    raw = sub.get("aliases") or ""
    if isinstance(raw, (list, tuple)):
        vals = list(raw)
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            vals = parsed
        elif raw.strip():
            vals = re.split(r"[\n,，;；]", raw)
        else:
            vals = []
    else:
        vals = []
    out, seen = [], set()
    for v in vals:
        s = str(v or "").strip()
        n = normalize(s)
        if s and n and n not in seen:
            seen.add(n)
            out.append(s)
    return out


def terms_of(sub: dict) -> list[str]:
    """订阅的全部匹配词：关键词 + 别名。"""
    kw = str(sub.get("keyword") or "").strip()
    return ([kw] if kw else []) + aliases_of(sub)


def _term_hit(term: str, text_norm: str, low: str) -> bool:
    kw_norm = normalize(term)
    if not kw_norm or kw_norm not in text_norm:
        return False
    # 短英文词的误报保护：归一化域太宽，退回原文按完整单词匹配
    if len(kw_norm) < 4 and kw_norm.isascii() and kw_norm.isalpha():
        return bool(re.search(rf"(?<![0-9a-z]){re.escape(kw_norm)}(?![0-9a-z])", low))
    return True


def match_all(text: str) -> list[dict]:
    """返回所有命中的启用中订阅（一条消息可同时命中多个订阅）。

    匹配策略（两级）：
    1. 归一化子串：双方去空格/标点、全角转半角、忽略大小写后做子串匹配；
    2. 短词边界保护：归一化后不足 4 个字符的纯英文短词（如 IT、Up）
       改用原文完整单词匹配，避免误命中 supernatural 这类长词。
    """
    if not text:
        return []
    low = str(text).lower()
    text_norm = normalize(text)
    hits = []
    for r in db.query("SELECT * FROM subscriptions WHERE enabled=1 ORDER BY id"):
        row = dict(r)
        if any(_term_hit(t, text_norm, low) for t in terms_of(row)):
            hits.append(row)
    return hits


def match(text: str) -> dict | None:
    """兼容旧调用：返回第一条命中的订阅。主流程请用 match_all。"""
    hits = match_all(text)
    return hits[0] if hits else None


def find_conflict(keyword: str, aliases=None, exclude_id: int | None = None) -> dict | None:
    """按归一化口径检查重复订阅（关键词与别名交叉比对）。"""
    incoming = {normalize(x) for x in [keyword, *(aliases or [])] if normalize(x)}
    if not incoming:
        return None
    for r in db.query("SELECT * FROM subscriptions ORDER BY id"):
        if exclude_id and r["id"] == exclude_id:
            continue
        row = dict(r)
        if incoming & {normalize(x) for x in terms_of(row) if normalize(x)}:
            return row
    return None


def _legacy_numbers(sub: dict) -> tuple[int | None, int | None]:
    """把旧版的字符串 last_ep 解析成 (季, 集)，用于平滑升级。"""
    if sub.get("last_episode") is not None:
        return sub.get("last_season"), sub.get("last_episode")
    old = str(sub.get("last_ep") or "")
    if not old:
        return None, None
    season, ep, _kind = episode.parse(old)
    return season, ep


def record_progress(sub: dict, title: str, season: int | None = None,
                    ep: int | None = None, kind: str = "ep") -> bool:
    """记录命中进度，返回是否为新的一集（可发追更通知）。

    同一集的不同写法（S01E05 / s1e5 / 第5集 / 5集）视为同一集，不重复通知；
    无集数标识的内容只在首次命中时通知。
    进度只前进不回退：旧集重发（S01E03 晚于 S02E05 到达）不推送、不倒退进度；
    进度取自数据库最新值（而非调用方传入的快照），并发命中同一订阅时以后到者
    与库中值单调比较，后写的旧集不会覆盖已推进的进度。
    """
    old_season, old_ep = _legacy_numbers(sub)
    old_display = str(sub.get("last_ep") or "")
    # 重新读取库中最新进度：调用方持有的 sub 可能是并发处理前的旧快照
    row = db.query(
        "SELECT last_ep, last_season, last_episode FROM subscriptions WHERE id=?",
        (sub["id"],), one=True)
    if row is not None:
        old_display = str(row["last_ep"] or "") or old_display
        if row["last_season"] is not None:
            old_season = int(row["last_season"])
        if row["last_episode"] is not None:
            old_ep = int(row["last_episode"])

    if ep is None:
        is_new = old_ep is None and not old_display
    elif old_ep is None:
        is_new = True
    elif season is not None and old_season is not None:
        # 只判「更新」不判「不同」：旧集重发不算新内容（同时防进度倒退）
        is_new = (int(season), int(ep)) > (int(old_season), int(old_ep))
    elif season is not None:
        # 旧记录没有季信息：按新集处理（最多重复一次通知，避免漏报）
        is_new = True
    else:
        is_new = int(ep) > int(old_ep)

    display = episode.canonical(season, ep, kind) if ep is not None else old_display
    db.execute(
        "UPDATE subscriptions SET last_ep=?, last_season=?, last_episode=?, last_title=?,"
        " last_match_at=? WHERE id=?",
        (display,
         season if ep is not None else old_season,
         ep if ep is not None else old_ep,
         (title or "")[:200], time.time(), sub["id"]))
    return is_new


def touch_match(sub: dict, title: str) -> None:
    """只更新「最近命中」展示字段，不推进追更进度（用于被跳过/未转存的命中）。"""
    db.execute(
        "UPDATE subscriptions SET last_title=?, last_match_at=? WHERE id=?",
        ((title or "")[:200], time.time(), sub["id"]))
