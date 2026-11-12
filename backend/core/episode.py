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
import os
import re

_VIDEO_EXTS = frozenset({
    ".mp4", ".mkv", ".ts", ".mov", ".avi", ".flv", ".wmv", ".m2ts", ".rmvb", ".iso",
})
_DUP_SUFFIX_RE = re.compile(r"[\s\._-]*[\(\[（【]\s*([1-9]\d{0,2})\s*[\)\]）】]$")

_SEASON_EP = re.compile(r"[Ss]0*(\d{1,2})[\s\.\-_]*[Ee](\d{1,3})")
# E12 / EP12：前面不能紧邻数字或字母（避免 S01E12 里的 E12、ED2K 之类误判）
_EP = re.compile(r"(?<![0-9A-Za-z])[Ee][Pp]?\s?(\d{1,3})(?![0-9])")
_CN = re.compile(r"第\s*(\d{1,3})\s*([集话話期])")
# 12集 / 12话 / 12期（无「第」字，频道常见写法）
_BARE = re.compile(r"(?<![0-9])(\d{1,3})\s*([集话話期])")

_RANGE_SEASON_EP = re.compile(
    r"[Ss]0*(\d{1,2})[\s\.\-_]*[Ee]0*(\d{1,3})\s*[-~～]\s*[Ee]?0*(\d{1,3})(?![0-9A-Za-z])"
)
_RANGE_CN_EP = re.compile(r"第\s*0*(\d{1,3})\s*[-~～至到]\s*0*(\d{1,3})\s*([集话話期])")
_RANGE_EP = re.compile(
    r"(?<![0-9A-Za-z])[Ee][Pp]?\s?0*(\d{1,3})\s*[-~～]\s*(?:[Ee][Pp]?)?0*(\d{1,3})(?![0-9A-Za-z])"
)
_PART_MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:part|pt|cd|disc|disk)[\s\._-]*0*(\d{1,2})(?![0-9A-Za-z])",
    re.IGNORECASE,
)
_CN_PART_RE = re.compile(
    r"(?:[\.\s_\-（(\[【]|(?:[Ss]\d{1,2}[Ee]\d{1,3}|第\d+[集话話期])[\.\s_\-]*)(上|中|下)(?=$|[\.\s_\-）)\]】])"
)

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


def extract_episode_keys(
    filename: str,
    folder_hint: str = "",
) -> list[tuple[int, int, str, str]]:
    """提取视频文件的标准季集签名列表 `[(season, episode, kind, part_tag), ...]`。

    仅对常见视频扩展名生效（字幕/图片等返回空列表，交由文件名查重处理）；
    支持范围集（如 S01E01-E02）、集内分段（Part1 / 上 / 下）及父目录季号补全；
    文件名与父目录均未标明季号时默认归为第 1 季，确保「S01E01」与「第1集」互相命中。
    """
    raw_root, ext = os.path.splitext(str(filename or "").strip())
    if ext.lower() not in _VIDEO_EXTS:
        return []
    stem = _DUP_SUFFIX_RE.sub("", raw_root).rstrip()
    if not stem:
        return []

    season = parse_season(stem)
    ep_start: int | None = None
    ep_end: int | None = None
    kind = "ep"

    m = _RANGE_SEASON_EP.search(stem)
    if m:
        s_val, st, ed = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 0 <= (ed - st) <= 30:
            season, ep_start, ep_end, kind = s_val, st, ed, "ep"

    if ep_start is None:
        m = _RANGE_CN_EP.search(stem)
        if m:
            st, ed, kch = int(m.group(1)), int(m.group(2)), m.group(3)
            if 0 <= (ed - st) <= 30:
                ep_start, ep_end, kind = st, ed, _EP_KINDS.get(kch, "ep")

    if ep_start is None:
        m = _RANGE_EP.search(stem)
        if m:
            st, ed = int(m.group(1)), int(m.group(2))
            if 0 <= (ed - st) <= 30:
                ep_start, ep_end, kind = st, ed, "ep"

    if ep_start is None:
        s_single, ep_single, kind = parse(stem)
        if ep_single is not None:
            if season is None:
                season = s_single
            ep_start = ep_end = ep_single

    if ep_start is None or ep_end is None:
        return []

    if season is None and folder_hint:
        season = parse_season(folder_hint)
    if season is None:
        season = 1

    part_tag = ""
    pm = _PART_MARKER_RE.search(stem)
    if pm:
        part_tag = f"part:{int(pm.group(1))}"
    else:
        cm = _CN_PART_RE.search(stem)
        if cm:
            part_tag = f"part:{cm.group(1)}"

    return [(int(season), ep, kind, part_tag) for ep in range(ep_start, ep_end + 1)]
