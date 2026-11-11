"""集数与季度标记解析：统一频道帖子与文件名中各种季度与集数写法。

三处共用，避免各写一套正则导致口径不一致：
- 转存通知 jobs.extract_episode（展示用）
- 订阅追更进度 subscribe.record_progress（数值比较）
- 自动文件整理 按季入库 分流（数值比较与目录定位）
- 向前补全 episode_backfill（同剧跨帖匹配）

季度支持：
- S01 / S1 / s01 / s1 / S01E05 / s1e5
- Season01 / Season1 / Season 01 / Season 1 / Season.01（大小写不敏感）
- 第一季 / 第1季 / 第01季 / 第 1 季 / 特别篇 / SP
"""
import re

_SEASON_EP = re.compile(r"[Ss]0*(\d{1,2})[\s\.\-_]*[Ee](\d{1,3})")
# E12 / EP12：前面不能紧邻数字或字母（避免 S01E12 里的 E12、ED2K 之类误判）
_EP = re.compile(r"(?<![0-9A-Za-z])[Ee][Pp]?\s?(\d{1,3})(?![0-9])")
_CN = re.compile(r"第\s*(\d{1,3})\s*([集话話期])")
# 12集 / 12话 / 12期（无「第」字，频道常见写法）
_BARE = re.compile(r"(?<![0-9])(\d{1,3})\s*([集话話期])")

_EP_KINDS = {"集": "ep", "话": "ep", "話": "ep", "期": "period"}

_CN_NUM_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20
}


def parse_season(text: str) -> int | None:
    """从文件名或标题中精确解析季号（1, 2, ...，特别篇为 0，解析不到返回 None）。
    完全兼容：Season01, Season 01, Season.01, Season1, S01, S1, season01, s01, s1,
             第一季, 第1季, 第01季, 第 1 季, 特别篇, SP 等（大小写不敏感）。
    """
    t = str(text or "").strip()
    if not t:
        return None
    # 1. 优先正规集数规范：S01E12 / S1E12 / S01.E12 / S1_E05
    m = _SEASON_EP.search(t)
    if m:
        return int(m.group(1))
    # 2. Season01 / Season1 / Season 01 / Season 1 / Season.01 / Season-01
    m = re.search(r"(?<![0-9a-zA-Z])season[\s\.\-_]*0*(\d{1,2})", t, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # 3. 中文：第一季 / 第1季 / 第01季 / 第 1 季
    m = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*季", t)
    if m:
        val = m.group(1).strip()
        if val.isdigit():
            return int(val)
        return _CN_NUM_MAP.get(val)
    # 4. 独立 S01 / S1 / s01 / s1（前后边界非字母数字）
    m = re.search(r"(?<![0-9a-zA-Z])[sS]0*(\d{1,2})(?![0-9a-zA-Z])", t)
    if m:
        return int(m.group(1))
    # 5. 特别篇 / SP / Specials（排除常规季数标记后再命中特别篇）
    if re.search(r"特别篇|(?<![a-zA-Z0-9])sp(?:\d{1,3})?(?![a-zA-Z])|specials?", t, re.IGNORECASE):
        return 0
    return None


def parse(text: str) -> tuple[int | None, int | None, str]:
    """解析 (季, 集/期号, 类型)。类型：ep=剧集，period=综艺期数。"""
    t = str(text or "")
    season = parse_season(t)
    m = _SEASON_EP.search(t)
    if m:
        return int(m.group(1)), int(m.group(2)), "ep"
    m = _EP.search(t)
    if m:
        return season, int(m.group(1)), "ep"
    m = _CN.search(t)
    if m:
        return season, int(m.group(1)), _EP_KINDS.get(m.group(2), "ep")
    m = _BARE.search(t)
    if m:
        return season, int(m.group(1)), _EP_KINDS.get(m.group(2), "ep")
    return season, None, "ep"


def canonical(season: int | None, episode: int | None, kind: str = "ep") -> str:
    """规范化展示：有季号用 S01E05，否则用「第N集 / 第N期」。"""
    if episode is None:
        return ""
    if season is not None:
        return f"S{int(season):02d}E{int(episode):02d}"
    return f"第{int(episode)}{'期' if kind == 'period' else '集'}"


def display(text: str) -> str:
    """从标题提取并规范化集数标识，提取不到返回空串。"""
    return canonical(*parse(text))


def strip_markers(text: str) -> str:
    """去掉标题里的集数标记（供「同剧」匹配使用）。"""
    t = str(text or "")
    t = _SEASON_EP.sub("", t)
    t = _EP.sub("", t)
    t = _CN.sub("", t)
    t = _BARE.sub("", t)
    return t
