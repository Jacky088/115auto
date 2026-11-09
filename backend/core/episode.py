"""集数标记解析：统一频道帖子里各种集数写法。

三处共用，避免各写一套正则导致口径不一致：
- 转存通知 jobs.extract_episode（展示用）
- 订阅追更进度 subscribe.record_progress（数值比较）
- 向前补全 episode_backfill（同剧跨帖匹配）

同一集的不同写法（S01E05 / s1e5 / EP5 / 第5集 / 5集）都解析为相同的
(季, 集号, 类型)，避免格式差异导致重复通知或漏通知。
"""
import re

_SEASON_EP = re.compile(r"[Ss](\d{1,2})\s?[Ee](\d{1,3})")
# E12 / EP12：前面不能紧邻数字或字母（避免 S01E12 里的 E12、ED2K 之类误判）
_EP = re.compile(r"(?<![0-9A-Za-z])[Ee][Pp]?\s?(\d{1,3})(?![0-9])")
_CN = re.compile(r"第\s*(\d{1,3})\s*([集话話期])")
# 12集 / 12话 / 12期（无「第」字，频道常见写法）
_BARE = re.compile(r"(?<![0-9])(\d{1,3})\s*([集话話期])")

_EP_KINDS = {"集": "ep", "话": "ep", "話": "ep", "期": "period"}


def parse(text: str) -> tuple[int | None, int | None, str]:
    """解析 (季, 集/期号, 类型)。类型：ep=剧集，period=综艺期数。"""
    t = str(text or "")
    m = _SEASON_EP.search(t)
    if m:
        return int(m.group(1)), int(m.group(2)), "ep"
    m = _EP.search(t)
    if m:
        return None, int(m.group(1)), "ep"
    m = _CN.search(t)
    if m:
        return None, int(m.group(1)), _EP_KINDS.get(m.group(2), "ep")
    m = _BARE.search(t)
    if m:
        return None, int(m.group(1)), _EP_KINDS.get(m.group(2), "ep")
    return None, None, "ep"


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
