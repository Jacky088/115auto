"""高精度媒体解析与 Emby/Jellyfin/Plex/Infuse 规范命名引擎。

核心设计：
1. 三层上下文联合提取：文件名 → 回溯父目录（跳过 Season/画质/分类目录）→ 转存/订阅数据库线索；
2. 状态机防误杀顺序：
   剥离 115 `(1)` 副本后缀 → 提取 `[tmdbid=xxx]` / `imdb` → 提取并隔离技术标签
   (`1920x1080` / `2160p` / `x265` / `10bit` / `60fps`) → 剥离字幕组与推广噪声 →
   解析季/集/特别篇/分卷 → 安全提取年份（保护 `1917`、`2001` 等数字片名）→ 分离中英文主副标题；
3. 配套字幕语言标签配对、BDMV 原盘整体保护、非法字符清洗与模板占位符格式化。
"""
from __future__ import annotations

import datetime
import os
import re
import string
from dataclasses import dataclass, field

from ..pan115.client import (
    _GENERIC_LIBRARY_SKIP_FOLDERS,
    normalize_title,
    parse_season_number,
    strip_duplicate_suffix,
)

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".ts", ".mov", ".avi", ".flv", ".wmv",
    ".m2ts", ".rmvb", ".iso", ".mpg", ".mpeg", ".m4v", ".webm",
}
SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt", ".sup", ".vtt", ".idx", ".sub"}
MEDIA_ALL_EXTENSIONS = VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS

# 仅包含非正片特征的关键词；NCOP/NCED/Menu 等常见 BD 特典一并归为花絮/额外内容
_EXTRA_TOKEN_RE = re.compile(
    r"(?:^|[\s\.\-_\[\(【（])("
    r"trailer|sample|teaser|preview|featurette|behindthescenes|deleted\s*scenes|"
    r"making[\s\._-]?of|interview|promo|ncop|nced|pv\d*|cm\d*|menu\d*|"
    r"预告片?|先导片?|宣传片?|制作特辑|幕后花絮?|花絮|彩蛋|片段|访谈|特典|样片|试看"
    r")(?=$|[\s\.\-_\]\)】）\d])",
    re.IGNORECASE,
)

# 特别篇（Emby/Jellyfin 规范归入 Season 00）
_SPECIAL_RE = re.compile(
    r"(?:^|[\s\.\-_\[\(【（])("
    r"specials?|ova|oad|oav|sp(?:\d{1,2})?|特别篇|番外篇?|sp合集"
    r")(?=$|[\s\.\-_\]\)】）\d])",
    re.IGNORECASE,
)

# 分段电影标识（CD1/CD2, Part1/Part2, Disc1/Disc2, pt1；保护 `下载`、`上海` 等正常片名）
_PART_RE = re.compile(
    r"(?:^|[\s\.\-_])("
    r"(?:cd|disc|disk|dvd|part|pt)[\s\._-]?0*(\d{1,2})(?=$|[\s\.\-_\]）)])|"
    r"[上下](?:[部集]|(?=$|[\s\.\-_\]）)]))|"
    r"第[一二三123][部集]?(?=$|[\s\.\-_\]）)])"
    r")",
    re.IGNORECASE,
)

# 显式 ID 标签
_TMDB_TAG_RE = re.compile(r"[\[\{（(]\s*tmdb(?:id)?[\s:=\-]+(\d+)\s*[\]\}）)]", re.IGNORECASE)
_IMDB_TAG_RE = re.compile(r"[\[\{（(]?\s*(tt\d{6,9})\s*[\]\}）)]?", re.IGNORECASE)

# 技术标签：在年份与集数识别前先抽出并清除，避免 1920/2160/264/265/10/60 误识别
_RES_PATTERNS = [
    (re.compile(r"(?:^|[\s\.\-_\[（(])(3840\s*[x×\*]\s*2160|4096\s*[x×\*]\s*2160|2160[pi]|4k|uhd)(?=$|[\s\.\-_\]）),])", re.I), "2160p"),
    (re.compile(r"(?:^|[\s\.\-_\[（(])(7680\s*[x×\*]\s*4320|4320[pi]|8k)(?=$|[\s\.\-_\]）),])", re.I), "4320p"),
    (re.compile(r"(?:^|[\s\.\-_\[（(])(1920\s*[x×\*]\s*1080|1080[pi])(?=$|[\s\.\-_\]）),])", re.I), "1080p"),
    (re.compile(r"(?:^|[\s\.\-_\[（(])(1280\s*[x×\*]\s*720|720[pi])(?=$|[\s\.\-_\]）),])", re.I), "720p"),
    (re.compile(r"(?:^|[\s\.\-_\[（(])(576[pi]|480[pi])(?=$|[\s\.\-_\]）),])", re.I), "480p"),
]
_SOURCE_RE = re.compile(
    r"(?:^|[\s\.\-_\[（(])(UHD[\s\.]?Blu-?Ray|Blu-?Ray|BDRemux|REMUX|BDRip|WEB-?DL|WEBRip|HDTV|DVDRip|HDTVRip)(?=$|[\s\.\-_\]）),])",
    re.IGNORECASE,
)
_VCODEC_RE = re.compile(
    r"(?:^|[\s\.\-_\[（(])([hx]\.?265|HEVC|[hx]\.?264|AVC|AV1|VC-?1|MPEG-?[24]|XviD)(?=$|[\s\.\-_\]）),])",
    re.IGNORECASE,
)
_ACODEC_RE = re.compile(
    r"(?:^|[\s\.\-_\[（(])(TrueHD(?:\s*Atmos)?|Atmos|DTS-?HD(?:\s*MA|\s*HR)?|DTS|DDP\s*5\.1|DD\+\s*5\.1|DD\s*5\.1|EAC3|AC3|AAC|FLAC|LPCM|Opus|MA\s*5\.1|MA\s*7\.1)(?=$|[\s\.\-_\]）),])",
    re.IGNORECASE,
)
_HDR_RE = re.compile(
    r"(?:^|[\s\.\-_\[（(])(DoVi|Dolby[\s\.]?Vision|DV|HDR10\+?|HLG|SDR)(?=$|[\s\.\-_\]）),])",
    re.IGNORECASE,
)
_TECH_MISC_RE = re.compile(
    r"(?:^|[\s\.\-_\[（(])("
    r"10-?bit|8-?bit|Hi10P|60fps|120fps|24fps|60hz|"
    r"chs|cht|sc|tc|gb|big5|jpsc|jptc|ass|srt|sup|"
    r"简繁内封|简繁外挂|简日双语|繁日双语|双语字幕?|中英字幕?|内封字幕?|外挂字幕?|特效字幕?|"
    r"国语|粤语|日语|英语|多音轨|双音轨|国粤双语|无删减|完整版|导演剪辑版|加长版|"
    r"REPACK|PROPER|iNTERNAL|EXTENDED|UNCUT|COMPLETE|FiNAL|MULTI|DUAL"
    r")(?=$|[\s\.\-_\]）),])",
    re.IGNORECASE,
)

# 字幕组 / 推广方括号与尾部发布组
_LEADING_BRACKET_RE = re.compile(r"^(?:\s*[\[【（(][^\]】）)]{1,48}[\]】）)]\s*)+")
_BRACKET_NOISE_RE = re.compile(r"[\[【][^\]】]{1,64}[\]】]")
_TRAILING_GROUP_RE = re.compile(r"[\-_@]([A-Za-z][A-Za-z0-9_]{1,18})$")
_TRAILING_NON_GROUP_RE = re.compile(r"^(?:e|ep|sp|cd|pt|part|disc|disk|dvd|s)\d+(?:v\d)?$", re.IGNORECASE)
_URL_AD_RE = re.compile(r"(?:https?://|www\.)[^\s\]）】]+|[A-Za-z0-9\-]+\.(?:com|net|org|tv|cc|vip|xyz)\b", re.I)

# 集数与范围（按精度降序尝试）
_RANGE_SE_EP_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])S0*(\d{1,2})\s*[\._\- ]?\s*E0*(\d{1,4})\s*[-~～]\s*E?0*(\d{1,4})(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_SINGLE_SE_EP_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])S0*(\d{1,2})\s*[\._\- ]?\s*E0*(\d{1,4})(?:v\d)?(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_X_SE_EP_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])0*(\d{1,2})x0*(\d{1,4})(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_CN_EP_RANGE_RE = re.compile(r"第\s*0*(\d{1,4})\s*[-~～]\s*0*(\d{1,4})\s*[集话話]")
_CN_EP_RE = re.compile(r"第\s*0*(\d{1,4})\s*[集话話期]")
_EP_PREFIX_RE = re.compile(r"(?:^|[^A-Za-z0-9])(?:EP?|SP)0*(\d{1,4})(?:v\d)?(?=$|[^A-Za-z0-9])", re.IGNORECASE)
_ANIME_DASH_EP_RE = re.compile(r"[\s_\.\]][\-–—]\s*0*(\d{1,4})(?:v\d)?(?=$|[\s\.\[_（(])")
_ANIME_BRACKET_EP_RE = re.compile(r"[\[【]\s*0*(\d{1,4})(?:v\d)?\s*[\]】]")
_BARE_NUM_RE = re.compile(r"^0*(\d{1,4})(?:v\d)?$")
_SEASON_INLINE_RE = re.compile(
    r"(?:^|[\s\.\-_\[（(])(?:season[\s\.\-_]*0*(\d{1,2})|S0*(\d{1,2})(?![Ex\d])|第\s*([一二三四五六七八九十百\d]+)\s*[季部])(?=$|[\s\.\-_\]）)])",
    re.IGNORECASE,
)
_TOTAL_EPS_RE = re.compile(r"全\s*\d+\s*[集话話]")

# 通用无片名目录名（父目录回溯时跳过）
_GENERIC_PARENT_DIRS = _GENERIC_LIBRARY_SKIP_FOLDERS | {
    "电影", "电视剧", "剧集", "动漫", "动画", "番剧", "综艺", "纪录片", "少儿", "媒体库",
    "自动转存", "云下载", "离线下载", "share", "offline", "downloads", "media", "movies",
    "tv", "tv shows", "anime", "series", "4k", "2160p", "1080p", "bdmv", "extras", "extra",
    "花絮", "预告", "全集", "正片", "中字", "国语", "粤语",
}

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(raw: str) -> int | None:
    """支持一~九十九及阿拉伯数字的季数解析。"""
    s = (raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    if "十" in s:
        parts = s.split("十", 1)
        tens = 1 if parts[0] == "" else _CN_DIGIT.get(parts[0], 0)
        ones = 0 if parts[1] == "" else _CN_DIGIT.get(parts[1], 0)
        return tens * 10 + ones
    return None


_SUB_LANG_TOKENS = {
    "chs", "cht", "chi", "zh", "zhs", "zht", "sc", "tc", "gb", "big5",
    "eng", "en", "jp", "jpn", "ja", "kor", "ko", "chs&eng", "cht&eng",
    "chs.eng", "cht.eng", "sc.jp", "tc.jp", "简", "繁", "中英", "双语", "特效",
}


@dataclass
class ParsedMedia:
    title: str = ""
    alt_title: str = ""
    year: str = ""
    tmdb_id: int | None = None
    imdb_id: str = ""
    media_type: str = "movie"   # movie | tv
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    abs_episode: int | None = None
    part: str = ""
    resolution: str = ""
    source: str = ""
    video_codec: str = ""
    audio_codec: str = ""
    hdr: str = ""
    group: str = ""
    is_extra: bool = False
    is_special: bool = False
    is_bdmv: bool = False
    subtitle_lang: str = ""
    ext: str = ""
    orig_name: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "alt_title": self.alt_title,
            "year": self.year,
            "tmdb_id": self.tmdb_id,
            "imdb_id": self.imdb_id,
            "media_type": self.media_type,
            "season": self.season,
            "episode": self.episode,
            "episode_end": self.episode_end,
            "abs_episode": self.abs_episode,
            "part": self.part,
            "resolution": self.resolution,
            "source": self.source,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "hdr": self.hdr,
            "group": self.group,
            "is_extra": self.is_extra,
            "is_special": self.is_special,
            "is_bdmv": self.is_bdmv,
            "subtitle_lang": self.subtitle_lang,
            "ext": self.ext,
        }


def sanitize_filename(name: str, max_bytes: int = 220) -> str:
    """将文件名/单级目录名清洗为兼容 115 / SMB / Windows 的安全名称并控制字节长度。"""
    s = re.sub(r"[\x00-\x1f\x7f]", "", (name or "")).strip()
    if not s:
        return ""
    s = s.replace(":", "：")
    s = re.sub(r'[\\/*?"<>|]', " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" .")
    # 按 UTF-8 字节安全截断（115 单级名 255 字节上限，预留余量防止带后缀超限）
    encoded = s.encode("utf-8")
    if len(encoded) > max_bytes:
        root, ext = os.path.splitext(s)
        ext_b = ext.encode("utf-8")
        allow = max(32, max_bytes - len(ext_b))
        cut = root.encode("utf-8")[:allow].decode("utf-8", errors="ignore").rstrip(" .")
        s = f"{cut}{ext}"
    return s


def resolution_rank(res: str) -> int:
    """分辨率优先级排名（用于洗版比较）：8K > 2160p > 1080p > 720p > 其他。"""
    r = (res or "").lower()
    if "4320" in r or "8k" in r:
        return 5
    if "2160" in r or "4k" in r:
        return 4
    if "1080" in r:
        return 3
    if "720" in r:
        return 2
    if "480" in r or "576" in r:
        return 1
    return 0


def split_bilingual_title(raw_title: str) -> tuple[str, str]:
    """拆分中英双语混合标题（如 `葬送的芙莉莲 Sousou no Frieren` → (`葬送的芙莉莲`, `Sousou no Frieren`)）。
    注意保留 `X战警`、`9号秘事`、`F1` 等天然混杂短前缀不误拆。
    """
    s = (raw_title or "").strip()
    if not s:
        return "", ""
    # 常见显式斜杠分隔：`狂飙 / The Knockout`
    if " / " in s:
        left, right = [p.strip() for p in s.split(" / ", 1)]
        if re.search(r"[\u4e00-\u9fa5]", left) and right:
            return left, right
        if re.search(r"[\u4e00-\u9fa5]", right) and left:
            return right, left
    m_cn = re.search(r"([\u4e00-\u9fa5][\u4e00-\u9fa50-9\s：:·\-]{0,60}[\u4e00-\u9fa50-9])", s)
    if not m_cn:
        return s, ""
    cn_part = m_cn.group(1).strip(" -:：")
    rest = (s[:m_cn.start()] + " " + s[m_cn.end():]).strip(" -:：/")
    # 英文部分至少 4 个字符或含空格的多词短语才作为备选英文名，避免拆散单字母片名
    if rest and (len(re.findall(r"[A-Za-z]", rest)) >= 4 and (" " in rest or any(c.islower() for c in rest))):
        return cn_part, re.sub(r"\s{2,}", " ", rest).strip()
    return s, ""


def _extract_tech_and_strip(work: str) -> tuple[str, dict]:
    """按固定顺序抽出分辨率、片源、编码、HDR、字幕组噪声，返回剩余干净文本与字段字典。"""
    meta = {
        "resolution": "",
        "source": "",
        "video_codec": "",
        "audio_codec": "",
        "hdr": "",
        "group": "",
    }
    s = _URL_AD_RE.sub(" ", work)

    # 提取片头字幕组 `[LoliHouse]`
    m_lead = _LEADING_BRACKET_RE.match(s)
    if m_lead:
        grp = re.sub(r"[\[\]（）()【】]", " ", m_lead.group(0)).strip()
        if grp and not grp.isdigit():
            meta["group"] = grp[:40]
        s = s[m_lead.end():]

    for pat, label in _RES_PATTERNS:
        if pat.search(s):
            if not meta["resolution"]:
                meta["resolution"] = label
            s = pat.sub(" ", s)

    for pat, key in ((_SOURCE_RE, "source"), (_VCODEC_RE, "video_codec"), (_ACODEC_RE, "audio_codec"), (_HDR_RE, "hdr")):
        m = pat.search(s)
        if m:
            meta[key] = m.group(1).strip()
            s = pat.sub(" ", s)

    s = _TECH_MISC_RE.sub(" ", s)

    # 尾部发布组（如 `-WiKi` `-FRDS`），避免误删单字母、或误把 `-E02` / `-CD1` / `-SP1` 当发布组吃掉
    m_grp = _TRAILING_GROUP_RE.search(s.rstrip())
    if m_grp and len(m_grp.group(1)) >= 2 and not _TRAILING_NON_GROUP_RE.fullmatch(m_grp.group(1)):
        if not meta["group"]:
            meta["group"] = m_grp.group(1)
        s = s[:m_grp.start()]

    return s, meta


def _strip_year_safely(s: str) -> tuple[str, str]:
    """从残余标题文本提取发行年份，保护 `1917`、`2001` 等数字片名不被挖空。"""
    now_hi = datetime.date.today().year + 2
    matches = []
    for m in re.finditer(r"(?:^|[\.\s_\(\[【（\-])((?:19|20)\d{2})(?=$|[\.\s_\)\]】）\-])", s):
        yr = int(m.group(1))
        if 1900 <= yr <= now_hi:
            bracketed = m.group(0).lstrip()[:1] in ("(", "[", "（", "【")
            matches.append((m.start(1), m.end(1), m.start(), m.group(1), bracketed))
    if not matches:
        return s, ""

    # 优先选括号内的最后一个合法年份，其次选最后一个不贴紧片首的合法年份
    bracket_hits = [it for it in matches if it[4]]
    chosen = bracket_hits[-1] if bracket_hits else matches[-1]

    # 如果唯一的年份就在最开头（如 `1917.mkv`）且前后都再无实质片名文字，保留不剥离
    before = re.sub(r"[\.\s_\(\[【（\-]+$", "", s[:chosen[2]]).strip()
    after = re.sub(r"^[\.\s_\)\]】）\-]+", "", s[chosen[1]:]).strip()
    if not before and not after:
        return s, ""
    if not before and after:
        # 类似 `2012.Doomsday` 或 `1917.The.Movie`：开头的数字更可能是片名，不盲当作年份
        if not chosen[4]:
            return s, ""

    year = chosen[3]
    # 常见结构：`片名 (2023) 副杂项` → 年份后面的多半是残留杂项；保留 `before` 作为片名
    cleaned = before if before else after
    return cleaned, year


def parse_media_filename(filename: str, is_dir: bool = False) -> ParsedMedia:
    """解析单个文件名或文件夹名，抽出结构化媒体特征。"""
    raw = (filename or "").strip()
    clean_raw, _ = strip_duplicate_suffix(raw)
    ext = ""
    if not is_dir:
        root, e = os.path.splitext(clean_raw)
        if e.lower() in MEDIA_ALL_EXTENSIONS:
            ext = e.lower()
            work = root
        else:
            work = clean_raw
            ext = e.lower()
    else:
        work = clean_raw

    pm = ParsedMedia(ext=ext, orig_name=raw)

    # 1. 显式 TMDB / IMDB 标签
    m_tmdb = _TMDB_TAG_RE.search(work)
    if m_tmdb:
        pm.tmdb_id = int(m_tmdb.group(1))
        work = _TMDB_TAG_RE.sub(" ", work)
    m_imdb = _IMDB_TAG_RE.search(work)
    if m_imdb:
        pm.imdb_id = m_imdb.group(1).lower()
        work = _IMDB_TAG_RE.sub(" ", work)

    # 2. 花絮/特别篇/分段检测
    if _EXTRA_TOKEN_RE.search(work):
        pm.is_extra = True
    if _SPECIAL_RE.search(work):
        pm.is_special = True
    m_part = _PART_RE.search(work)
    if m_part:
        pt_num = m_part.group(2)
        if pt_num:
            pm.part = f"part{int(pt_num)}"
        else:
            raw_pt = m_part.group(1)
            pm.part = "part1" if ("上" in raw_pt or "一" in raw_pt) else ("part2" if ("下" in raw_pt or "二" in raw_pt) else "part3")
        work = _PART_RE.sub(" ", work)

    # 3. 技术标签保护性剥离（此时方括号里的 [05] 等集数尚未剥离）
    work, tech = _extract_tech_and_strip(work)
    pm.resolution = tech["resolution"]
    pm.source = tech["source"]
    pm.video_codec = tech["video_codec"]
    pm.audio_codec = tech["audio_codec"]
    pm.hdr = tech["hdr"]
    pm.group = tech["group"]

    # 4. 集数解析（按确定性从高到低）
    ep_cut_idx: int | None = None
    m = _RANGE_SE_EP_RE.search(work)
    if m:
        pm.season = int(m.group(1))
        pm.episode = int(m.group(2))
        pm.episode_end = int(m.group(3))
        ep_cut_idx = m.start()
    else:
        m = _SINGLE_SE_EP_RE.search(work)
        if m:
            pm.season = int(m.group(1))
            pm.episode = int(m.group(2))
            ep_cut_idx = m.start()
        else:
            m = _X_SE_EP_RE.search(work)
            if m:
                pm.season = int(m.group(1))
                pm.episode = int(m.group(2))
                ep_cut_idx = m.start()

    if pm.episode is None:
        for pat in (_CN_EP_RANGE_RE, _CN_EP_RE):
            m = pat.search(work)
            if m:
                pm.episode = int(m.group(1))
                if pat is _CN_EP_RANGE_RE:
                    pm.episode_end = int(m.group(2))
                ep_cut_idx = m.start()
                break

    if pm.episode is None:
        m = _EP_PREFIX_RE.search(work)
        if m:
            pm.episode = int(m.group(1))
            ep_cut_idx = m.start()

    if pm.episode is None:
        m = _ANIME_DASH_EP_RE.search(work)
        if m:
            num = int(m.group(1))
            if not (1900 <= num <= datetime.date.today().year + 2):
                pm.episode = num
                ep_cut_idx = m.start()

    if pm.episode is None:
        m = _ANIME_BRACKET_EP_RE.search(work)
        if m:
            num = int(m.group(1))
            if not (1900 <= num <= datetime.date.today().year + 2):
                pm.episode = num
                ep_cut_idx = m.start()

    if pm.episode is None and not is_dir:
        m = _BARE_NUM_RE.fullmatch(work.strip())
        if m:
            num = int(m.group(1))
            if num <= 1899:
                pm.episode = num
                work = ""

    # 季数提取（如果上面未由 SxxExx 决定）
    m_sn = _SEASON_INLINE_RE.search(work)
    if m_sn:
        val = m_sn.group(1) or m_sn.group(2)
        if val is not None:
            if pm.season is None:
                pm.season = int(val)
        elif m_sn.group(3):
            parsed_cn = cn_to_int(m_sn.group(3))
            if parsed_cn is not None and pm.season is None:
                pm.season = parsed_cn
        work = _SEASON_INLINE_RE.sub(" ", work)

    if pm.is_special and pm.season is None:
        pm.season = 0

    # 如果从标题后半段抽出了集数标记，优先保留集数标记之前的主体
    if ep_cut_idx is not None and ep_cut_idx > 1:
        work = work[:ep_cut_idx]
    else:
        for p in (_RANGE_SE_EP_RE, _SINGLE_SE_EP_RE, _X_SE_EP_RE, _CN_EP_RANGE_RE, _CN_EP_RE, _EP_PREFIX_RE, _ANIME_DASH_EP_RE, _ANIME_BRACKET_EP_RE):
            work = p.sub(" ", work)

    work = _TOTAL_EPS_RE.sub(" ", work)
    # 清除残留的纯标签方括号，如 `[简繁内封]`
    work = _BRACKET_NOISE_RE.sub(" ", work)
    work = _EXTRA_TOKEN_RE.sub(" ", work)
    work = _SPECIAL_RE.sub(" ", work)

    # 5. 安全提取年份
    title_cand = re.sub(r"[\._]+", " ", work)
    title_cand = re.sub(r"\s{2,}", " ", title_cand).strip(" -·|")
    title_cand, yr = _strip_year_safely(title_cand)
    pm.year = yr

    # 6. 最终标题润色与中英双语分离
    title_cand = re.sub(r"[\(\[（【\)\]）】]+$", "", title_cand).strip(" .-·|")
    title_cand = re.sub(r"^[\(\[（【\)\]）】]+", "", title_cand).strip(" .-·|")
    if title_cand:
        pm.title, pm.alt_title = split_bilingual_title(title_cand)
        # 清洗英文部分中的点号残留
        if pm.alt_title:
            pm.alt_title = re.sub(r"\s{2,}", " ", pm.alt_title).strip()

    # 7. 类型初判
    if pm.episode is not None or pm.season is not None or _TOTAL_EPS_RE.search(clean_raw):
        pm.media_type = "tv"
        if pm.season is None:
            pm.season = 0 if pm.is_special else 1
        if pm.episode is not None and pm.episode > 50 and pm.season == 1:
            pm.abs_episode = pm.episode
    else:
        pm.media_type = "movie"

    return pm


def parse_media_context(
    filename: str,
    parent_dirs: list[str] | None = None,
    db_hint: dict | None = None,
    is_dir: bool = False,
) -> ParsedMedia:
    """三层上下文联合解析：文件名 → 父目录逐层向上补全 → 数据库记录线索兜底。"""
    pm = parse_media_filename(filename, is_dir=is_dir)

    # 向上遍历父目录（closest parent -> root）
    dirs_rev = list(reversed(parent_dirs or []))
    for dname in dirs_rev:
        dclean = (dname or "").strip()
        if not dclean:
            continue
        sn = parse_season_number(dclean)
        if sn is not None:
            if pm.season is None or (pm.season == 1 and sn != 1):
                pm.season = sn
            pm.media_type = "tv"
            if sn == 0:
                pm.is_special = True
            continue
        if (
            dclean.lower() in _GENERIC_PARENT_DIRS
            or re.fullmatch(r"[\d\W]+", dclean)
            or re.fullmatch(r".{0,6}(?:下载|临时|转存|整理|默认|未分类|待刮削|待整理|媒体|资源)(?:目录|文件夹|区|库)", dclean, re.I)
        ):
            continue

        d_parsed = parse_media_filename(dclean, is_dir=True)
        # 若文件名本身就是纯集数（01.mp4）或无片名/缺英文原名/缺年份/缺 tmdb_id，则从父目录补全
        if not pm.title and d_parsed.title:
            pm.title = d_parsed.title
            pm.alt_title = pm.alt_title or d_parsed.alt_title
        elif pm.title and not pm.alt_title and d_parsed.alt_title:
            pm.alt_title = d_parsed.alt_title
        if not pm.year and d_parsed.year:
            pm.year = d_parsed.year
        if pm.tmdb_id is None and d_parsed.tmdb_id is not None:
            pm.tmdb_id = d_parsed.tmdb_id
        if not pm.imdb_id and d_parsed.imdb_id:
            pm.imdb_id = d_parsed.imdb_id
        if d_parsed.media_type == "tv":
            pm.media_type = "tv"
            if pm.season is None and d_parsed.season is not None:
                pm.season = d_parsed.season
        # 只取到最近一层有效的片名目录即可，避免被再上一级的合集大类目录覆盖
        if pm.title and pm.year:
            break

    # 数据库提示兜底（transfers / subscriptions）
    if db_hint:
        if pm.tmdb_id is None and db_hint.get("tmdb_id"):
            try:
                pm.tmdb_id = int(db_hint["tmdb_id"])
            except (TypeError, ValueError):
                pass
        hint_kind = (db_hint.get("kind") or "").strip().lower()
        if hint_kind in ("tv", "movie") and pm.episode is None and pm.season is None:
            pm.media_type = hint_kind
        hint_title = (db_hint.get("title") or "").strip()
        if not pm.title and hint_title:
            h_parsed = parse_media_filename(hint_title, is_dir=True)
            pm.title = h_parsed.title or hint_title
            pm.alt_title = pm.alt_title or h_parsed.alt_title
            if not pm.year and h_parsed.year:
                pm.year = h_parsed.year

    if pm.media_type == "tv" and pm.season is None:
        pm.season = 0 if pm.is_special else 1
    return pm


def match_subtitle_for_video(video_name: str, sub_names: list[str]) -> list[tuple[str, str]]:
    """在同一目录内匹配与视频主文件配套的外挂字幕，返回 [(原字幕文件名, 语言后缀如 '.chs')]。"""
    v_clean, _ = strip_duplicate_suffix(video_name)
    v_stem = os.path.splitext(v_clean)[0]
    if not v_stem:
        return []
    v_norm = normalize_title(v_stem, fuzzy_t2s=True)
    v_parsed = parse_media_filename(v_clean)
    matched: list[tuple[str, str]] = []
    for sn in sub_names:
        s_clean, _ = strip_duplicate_suffix(sn)
        s_root, s_ext = os.path.splitext(s_clean)
        if s_ext.lower() not in SUBTITLE_EXTENSIONS:
            continue
        lang_tag = ""
        is_hit = False
        # 方式 1：原前缀直接匹配（兼容带语言标签 `片名.S01E01.chs.ass`）
        if s_root == v_stem:
            is_hit = True
        elif s_root.lower().startswith(v_stem.lower() + "."):
            is_hit = True
            suffix = s_root[len(v_stem) + 1:].strip(".")
            parts = [p for p in suffix.split(".") if p]
            lang_parts = [p for p in parts if p.lower() in _SUB_LANG_TOKENS or len(p) <= 10]
            if lang_parts:
                lang_tag = "." + ".".join(lang_parts[:2])
        else:
            # 方式 2：归一化比对（忽略点号/下划线/空格差异）
            s_norm = normalize_title(s_root, fuzzy_t2s=True)
            if v_norm and (s_norm == v_norm or s_norm.startswith(v_norm)):
                is_hit = True
            elif v_parsed.media_type == "tv" and v_parsed.episode is not None:
                # 方式 3：同目录按季/集号兜底配对（解决 `狂飙.S01E01.2160p.mkv` 与 `狂飙.S01E01.chs.ass`）
                s_parsed = parse_media_filename(s_clean)
                if (
                    s_parsed.episode == v_parsed.episode
                    and (s_parsed.season or 1) == (v_parsed.season or 1)
                ):
                    vn_t = normalize_title(v_parsed.title, fuzzy_t2s=True)
                    sn_t = normalize_title(s_parsed.title, fuzzy_t2s=True)
                    if not sn_t or not vn_t or sn_t == vn_t or sn_t in vn_t or vn_t in sn_t:
                        is_hit = True
            if is_hit and "." in s_root:
                tail = s_root.rsplit(".", 1)[-1].strip()
                if tail.lower() in _SUB_LANG_TOKENS:
                    lang_tag = f".{tail}"
        if is_hit:
            matched.append((sn, lang_tag))
    return matched


DEFAULT_MOVIE_TEMPLATE = "{title} ({year}){tmdb_tag}/{title} ({year}){part_tag}{res_tag}{ext}"
DEFAULT_TV_TEMPLATE = "{title} ({year}){tmdb_tag}/Season {season_00}/{title} - S{season_00}E{episode_00}{part_tag}{ext}"

_ALLOWED_TEMPLATE_FIELDS_MOVIE = frozenset({
    "title", "year", "year_tag", "tmdb_id", "tmdb_tag",
    "part_tag", "res_tag", "resolution", "ext",
})
_ALLOWED_TEMPLATE_FIELDS_TV = _ALLOWED_TEMPLATE_FIELDS_MOVIE | frozenset({
    "season", "season_00", "episode", "episode_00",
})
_PLAIN_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FMT = string.Formatter()


def validate_organize_template(template: str, kind: str = "movie") -> str:
    """校验命名模板仅包含白名单纯占位符（禁止属性访问、下标切片与格式说明符注入）。"""
    tpl = str(template or "").strip()
    if not tpl:
        return ""
    if len(tpl) > 400:
        raise ValueError("命名模板长度不能超过 400 字符")
    allowed = _ALLOWED_TEMPLATE_FIELDS_TV if kind == "tv" else _ALLOWED_TEMPLATE_FIELDS_MOVIE
    try:
        tokens = list(_FMT.parse(tpl))
    except ValueError as e:
        raise ValueError(f"命名模板花括号语法无效: {e}") from e

    for _, field_name, format_spec, conversion in tokens:
        if field_name is None:
            continue
        if conversion:
            raise ValueError(f"命名模板不支持转换标记 !{conversion}")
        if format_spec:
            raise ValueError(f"命名模板不支持自定义格式说明符 :{format_spec}")
        if not _PLAIN_FIELD_RE.fullmatch(field_name) or field_name not in allowed:
            raise ValueError(f"命名模板包含不支持或非法占位符 {{{field_name}}}")
    return tpl


def _render_safe_template(
    tpl: str,
    mapping: dict[str, str],
    kind: str,
    fallback_tpl: str,
) -> str:
    """逐段替换白名单占位符（绕开 `str.format_map` 属性/下标求值，异常时回退默认模板）。"""
    try:
        safe_tpl = validate_organize_template(tpl, kind=kind) or fallback_tpl
    except ValueError:
        safe_tpl = fallback_tpl

    tpl_clean = re.sub(r"\s*\(\{year\}\)", "{year_tag}", safe_tpl)
    out: list[str] = []
    for literal_text, field_name, _, _ in _FMT.parse(tpl_clean):
        if literal_text:
            out.append(literal_text)
        if field_name is not None:
            out.append(str(mapping.get(field_name, "")))
    return "".join(out)


def format_media_target(
    parsed: ParsedMedia,
    settings: dict,
    tmdb_meta: dict | None = None,
) -> tuple[str, str]:
    """根据标准命名模板与（可选）TMDB 权威元数据生成 `(相对目录路径, 规范文件名)`。

    例：(`狂飙 (2023) [tmdbid=215403]/Season 01`, `狂飙 - S01E01.mkv`)
    """
    title = (tmdb_meta or {}).get("title") or parsed.title or "未命名媒体"
    year = str((tmdb_meta or {}).get("year") or parsed.year or "").strip()
    tmdb_id = (tmdb_meta or {}).get("tmdb_id") or parsed.tmdb_id
    media_type = (tmdb_meta or {}).get("media_type") or parsed.media_type or "movie"
    append_tmdb = bool(settings.get("media_append_tmdb_tag", True))

    safe_title = sanitize_filename(title)
    year_seg = f" ({year})" if year else ""
    tmdb_seg = f" [tmdbid={tmdb_id}]" if (append_tmdb and tmdb_id) else ""
    res_seg = f" - {parsed.resolution}" if parsed.resolution else ""
    part_seg = f" - {parsed.part}" if parsed.part else ""

    if media_type == "tv":
        season = 0 if parsed.is_special else (parsed.season if parsed.season is not None else 1)
        ep = parsed.episode if parsed.episode is not None else 1
        s00 = f"{season:02d}"
        if parsed.episode_end and parsed.episode_end > ep:
            e00 = f"{ep:02d}-E{parsed.episode_end:02d}"
        else:
            e00 = f"{ep:02d}"
        default_tpl = DEFAULT_TV_TEMPLATE
        tpl = str(settings.get("media_organize_template_tv") or default_tpl)
        mapping = {
            "title": safe_title,
            "year": year,
            "year_tag": year_seg,
            "tmdb_id": str(tmdb_id or ""),
            "tmdb_tag": tmdb_seg,
            "season": str(season),
            "season_00": s00,
            "episode": str(ep),
            "episode_00": e00,
            "part_tag": part_seg,
            "res_tag": res_seg,
            "resolution": parsed.resolution,
            "ext": parsed.ext,
        }
    else:
        default_tpl = DEFAULT_MOVIE_TEMPLATE
        tpl = str(settings.get("media_organize_template_movie") or default_tpl)
        mapping = {
            "title": safe_title,
            "year": year,
            "year_tag": year_seg,
            "tmdb_id": str(tmdb_id or ""),
            "tmdb_tag": tmdb_seg,
            "part_tag": part_seg,
            "res_tag": res_seg,
            "resolution": parsed.resolution,
            "ext": parsed.ext,
        }

    rendered = _render_safe_template(tpl, mapping, "tv" if media_type == "tv" else "movie", default_tpl)
    rendered = re.sub(r"\s{2,}", " ", rendered).strip("/")
    parts = [p for seg in rendered.split("/") if (p := sanitize_filename(seg))]
    if len(parts) >= 2:
        rel_dir = "/".join(parts[:-1])
        fname = parts[-1]
    else:
        rel_dir = sanitize_filename(f"{safe_title}{year_seg}{tmdb_seg}".strip()) or "unsorted"
        fname = parts[0] if parts else sanitize_filename(f"{safe_title or 'media'}{parsed.ext}")
    return rel_dir, fname
