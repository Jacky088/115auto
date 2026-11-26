"""115 定时整理引擎的标题归一化、季号解析、广告清洗与跨库同名歧义预检工具函数。"""
from __future__ import annotations

import os
import re

from ..core.t2s import t2s


def _pan():
    """延迟获取 client 模块引用，避免循环导入且保证测试 patch 透明生效。"""
    from . import client
    return client


_CHINESE_CHAR_RE = re.compile(r"[一-龥]")

_MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".ts", ".mov", ".avi", ".flv", ".wmv", ".m2ts", ".rmvb", ".iso",
    ".srt", ".ass", ".vtt", ".sub"
}
_EXTRA_KEYWORDS = (
    "预告", "预告片", "花絮", "特辑", "彩蛋", "片段", "宣传片", "制作特辑",
    "sample", "trailer", "preview", "teaser", "extra", "behindthescenes", "featurette"
)
_ROMAN_NUMS = {
    "Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5",
    "Ⅵ": "6", "Ⅶ": "7", "Ⅷ": "8", "Ⅸ": "9", "Ⅹ": "10",
    "ⅰ": "1", "ⅱ": "2", "ⅲ": "3", "ⅳ": "4", "ⅴ": "5",
}
_CN_NUM_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20
}
_GENERIC_CN_TERMS = {
    "第一季", "第二季", "第三季", "第四季", "第五季", "第六季", "第七季", "第八季",
    "特别篇", "剧场版", "全集", "完结", "合集", "更新", "重制版", "电影版"
}


def extract_chinese_subject(kw: str) -> str:
    """从包含中英文的关键词中提取最长连续纯中文主体（排除通用词缀，长度>=2）。"""
    if not kw:
        return ""
    cn_chunks = re.findall(r"[\u4e00-\u9fa5]+", kw)
    if not cn_chunks:
        return ""
    valid_chunks = [c for c in cn_chunks if len(c) >= 2 and c not in _GENERIC_CN_TERMS]
    if not valid_chunks:
        return ""
    return max(valid_chunks, key=len)


_GENERIC_LIBRARY_SKIP_FOLDERS = {
    "其他", "未整理", "待整理", "待分类", "临时", "新建文件夹", "temp", "trash", "recycle", "lost+found"
}

_DUP_SUFFIX_RE = re.compile(r"[\s\._-]*[\(\[（【]\s*([1-9]\d{0,2})\s*[\)\]）】]$")


def strip_duplicate_suffix(filename: str) -> tuple[str, int]:
    """剥离 115 重复转存时自动追加的 (1)、(2)、（1）、（2）、 (1) 等冲突后缀。
    返回 (clean_name, dup_index)：
    - 若无后缀：返回 (原文件名, 0)
    - 若有后缀：返回 (去除后缀后的规范文件名, 后缀编号数字)
    同时兼容半角括号 ()、全角括号（）、中括号 []、全角中括号【】及有无前置空格。
    排除 4 位年份（如 2019、2024），避免误伤正片标题年份。
    """
    root, ext = os.path.splitext(filename or "")
    m = _DUP_SUFFIX_RE.search(root)
    if m:
        clean_root = root[:m.start()].rstrip()
        try:
            dup_idx = int(m.group(1))
        except ValueError:
            dup_idx = 1
        return f"{clean_root}{ext}", dup_idx
    return filename or "", 0


def parse_season_number(folder_name: str) -> int | None:
    """从文件夹名称中解析季度数字（1, 2, ...），特别篇为 0，解析不到返回 None。
    完全兼容：Season01, Season 01, Season.01, Season1, S01, S1, season01, s01, s1,
             第一季, 第1季, 第01季, 第 1 季, 特别篇, SP 等（大小写不敏感）。
    """
    name = (folder_name or "").strip()
    if not name:
        return None
    cleaned = re.sub(r"^[\(\[\{（【《「『]|[\)\]\}）】》」』]$", "", name).strip()
    if cleaned != name:
        return parse_season_number(cleaned)
    m = re.search(r"^(?:season[\s\.\-_]*|[sS])0*(\d{1,2})$", name.strip(), re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"^第\s*([一二三四五六七八九十\d]+)\s*季$", name.strip())
    if m:
        val = m.group(1).strip()
        if val.isdigit():
            return int(val)
        return _CN_NUM_MAP.get(val)
    if re.search(r"^(?:特别篇|specials?|sp(?:\s*\d{1,3})?)$", name.strip(), re.IGNORECASE):
        return 0
    return None


def normalize_title(s: str, fuzzy_t2s: bool = True) -> str:
    """标题软化归一化：小写 + 简繁自适应 + 罗马数字转阿拉伯数字 + 去除所有标点符号与空白字符。"""
    if not s:
        return ""
    if fuzzy_t2s:
        s = t2s(s)
    s = s.lower()
    for rom, arab in _ROMAN_NUMS.items():
        s = s.replace(rom.lower(), arab)
    return re.sub(r"[^\w\u4e00-\u9fa5]+|_", "", s)


def extract_target_folder_name(target_dir: str) -> str:
    """从目标目录路径中提取匹配关键词：
    1. 优先取末级目录，若末级为季目录（如 Season 1、第1季、特别篇等），自动回溯上级剧名目录；
    2. 若回溯后仍无中文（如 /电视剧/Drama/Season 1），尝试继续向上找包含中文的目录；
    3. 自动剥离常见的年份后缀（如 (2023)、[2024]、.2023 等），提取出纯净剧名。
    """
    parts = [p.strip() for p in (target_dir or "").strip().strip("/").split("/") if p.strip()]
    if not parts:
        return ""
    idx = len(parts) - 1
    if idx > 0 and parse_season_number(parts[idx]) is not None:
        idx -= 1
    candidate = parts[idx]
    if not _CHINESE_CHAR_RE.search(candidate) and idx > 0 and _CHINESE_CHAR_RE.search(parts[idx - 1]):
        candidate = parts[idx - 1]
    cleaned = re.sub(r"(?:[\s\.\-_]*[\(\[\{（【]?\s*(?:19\d{2}|20\d{2})\s*[\)\]\}）】]?)+$", "", candidate).strip()
    return cleaned or candidate


_AUTO_MOVE_SKIP_SCAN_DIRS = frozenset({
    "@eadir", ".ds_store", "__macosx", "_待清理旧版", "_待清理重复",
    "sample", "samples", "subs", "subtitles", "extras", "extra",
    "bdmv", "certificate", "video_ts",
})
_AUTO_MOVE_SCAN_MAX_DIRS = 50
# 云下载目录模式的垃圾文件体积下限：真媒体为 GB 级，广告样品只有几百 KB
_DIR_MATCH_MIN_SIZE = 10 * 1024 * 1024
# 广告括号段：【更多高清影视请访问 www.xxx.com】/（www.xxx.com）等——比标题还长，匹配前必须剥离
_AD_BRACKET_RE = re.compile(
    r"【[^】]*(?:www\.|http)[^】]*】"
    r"|（[^）]*(?:www\.|http)[^）]*）"
    r"|\([^)]*(?:www\.|http)[^)]*\)", re.IGNORECASE)


def _strip_ad_brackets(name: str) -> str:
    """剥离目录名中含 www/http 的广告括号段（【…】/（…）/(…) 三种括号），还原真实标题。"""
    cleaned = _AD_BRACKET_RE.sub(" ", name or "")
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _should_skip_auto_move_dir(dir_name: str) -> bool:
    low = (dir_name or "").strip().lower()
    if not low or low in _AUTO_MOVE_SKIP_SCAN_DIRS:
        return True
    return any(extra in low for extra in _EXTRA_KEYWORDS)


def collect_library_keyword_conflicts(targets: list[str], fuzzy_t2s: bool = True,
                                      scan_cache: dict | None = None) -> set[str]:
    """跨规则同名歧义预检：返回被 ≥2 个总库同时拥有的剧名关键词集合。

    对每个总库目标列出一级子目录，按与 move_matched_files_for_rule 完全相同的口径
    提取/归一化剧名关键词（剥年份、繁简互通、排除季目录与通用文件夹）；出现在
    ≥2 个库的关键词即为"两库同名"——无集数标识的裸文件名命中它们时不自动处理。
    targets 少于 2 个（去重后）时直接返回空集：单库不存在同名歧义——
    注意必须先去重，同一条库规则出现两次会让"整库剧名"都被误判为两库同名。
    复用 scan_cache 的目录缓存：预检列过的总库根目录，移动阶段零重复请求。
    """
    # 去重（保序）：同一总库被多条规则指向时只算一个库
    targets = list(dict.fromkeys((t or "").strip() for t in (targets or []) if (t or "").strip()))
    if len(targets) < 2:
        return set()
    entries_cache: dict[int, list[dict]] = \
        scan_cache.setdefault("dir_entries", {}) if scan_cache is not None else {}
    per_rule: list[set[str]] = []
    for t in targets:
        t = (t or "").strip()
        if not t:
            per_rule.append(set())
            continue
        try:
            cid = _pan().mkdirs(t)
        except Exception as e:
            if _pan()._hits_risk_keyword(str(e).lower()):
                raise
            cid = 0
        if not cid:
            per_rule.append(set())
            continue
        if cid in entries_cache:
            entries = entries_cache[cid]
        else:
            try:
                entries = _pan().list_entries(cid, limit=2000)
            except Exception as e:
                if _pan()._hits_risk_keyword(str(e).lower()):
                    raise
                entries = []
            if scan_cache is not None:
                entries_cache[cid] = entries
        kws: set[str] = set()
        for e in entries:
            if not e.get("is_dir"):
                continue
            nm = str(e.get("name") or "").strip()
            if not nm or nm.lower() in _GENERIC_LIBRARY_SKIP_FOLDERS:
                continue
            if parse_season_number(nm) is not None:
                continue
            if not _CHINESE_CHAR_RE.search(nm):
                continue
            nk = normalize_title(extract_target_folder_name(nm), fuzzy_t2s=fuzzy_t2s)
            if len(nk) >= 2:
                kws.add(nk)
        per_rule.append(kws)
    seen: set[str] = set()
    ambiguous: set[str] = set()
    for ks in per_rule:
        ambiguous |= (seen & ks)
        seen |= ks
    return ambiguous
