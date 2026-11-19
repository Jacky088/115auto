"""媒体解析与格式化引擎所用正则表达式、技术标签词表与模板白名单常量。"""
from __future__ import annotations

import re
import string

from ..pan115.client import _GENERIC_LIBRARY_SKIP_FOLDERS

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
# 注意「第N集」是集数不是分段：分段只认「第N部」，避免狂飙 第1集～第3集 被误判为 part 导致同名碰撞
_PART_RE = re.compile(
    r"(?:^|[\s\.\-_])("
    r"(?:cd|disc|disk|dvd|part|pt)[\s\._-]?0*(\d{1,2})(?=$|[\s\.\-_\]）)])|"
    r"[上下](?:[部集]|(?=$|[\s\.\-_\]）)]))|"
    r"第[一二三123]部?(?=$|[\s\.\-_\]）)])"
    r")",
    re.IGNORECASE,
)

# 显式 ID 标签（IMDB 要求前后边界，防止 BestTt123456 之类子串误提取）
_TMDB_TAG_RE = re.compile(r"[\[\{（(]\s*tmdb(?:id)?[\s:=\-]+(\d+)\s*[\]\}）)]", re.IGNORECASE)
_IMDB_TAG_RE = re.compile(
    r"(?:[\[\{（(]\s*|(?<![A-Za-z0-9]))(tt\d{6,9})(?:\s*[\]\}）)]|(?![0-9A-Za-z]))",
    re.IGNORECASE,
)

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
# 季号放宽到 4 位以兼容综艺「S2024E15」年季格式；集数放宽到 4 位兼容千集长寿番
_RANGE_SE_EP_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])S0*(\d{1,4})\s*[\._\- ]?\s*E0*(\d{1,4})\s*[-~～]\s*E?0*(\d{1,4})(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_SINGLE_SE_EP_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])S0*(\d{1,4})\s*[\._\- ]?\s*E0*(\d{1,4})(?:v\d)?(?:E0*(\d{1,4}))?(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_X_SE_EP_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])0*(\d{1,2})x0*(\d{1,4})(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_CN_EP_RANGE_RE = re.compile(r"第\s*0*(\d{1,4})\s*[-~～]\s*0*(\d{1,4})\s*[集话話]")
# 中文数字集数（第十二集 / 一百零三话）与阿拉伯数字统一解析
_CN_EP_RE = re.compile(r"第\s*(\d{1,4}|[零一二三四五六七八九十百两]{1,6})\s*[集话話期]")
# E1P2 / E01P2（分 P 双集文件）：集号后允许跟 P 段号
_EP_PREFIX_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:EP?|SP)0*(\d{1,4})(?:v\d)?(?:P0*(\d{1,2}))?(?=$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
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

_SUB_LANG_TOKENS = {
    "chs", "cht", "chi", "zh", "zhs", "zht", "sc", "tc", "gb", "big5",
    "eng", "en", "jp", "jpn", "ja", "kor", "ko", "chs&eng", "cht&eng",
    "chs.eng", "cht.eng", "sc.jp", "tc.jp", "简", "繁", "中英", "双语", "特效",
}

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
