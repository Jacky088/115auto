"""TMDB 刮削（可选启用）：把频道标题规范成官方名称 + 年份，用于目录命名与通知展示。

- 按分类选 TV / Movie 搜索接口，取首个结果（TMDB 相关性排序）
- 结果进程内缓存（同一剧集的多次转存只查一次）
- 任何失败（未配置/网络/限流）都静默降级为不刮削，绝不影响转存主流程
- TMDB 在大陆无法直连，复用设置页的 TG 代理
"""
import re
import threading
import time

import httpx

from .. import database as db

_CACHE: dict[str, tuple[float, dict | None]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 7 * 86400
_TV_CATS = {"电视剧", "动漫", "综艺"}

# 影响搜索命中率的噪声片段：集数、画质、发布组标签等
_NOISE_RE = re.compile(
    r"[Ss]\d{1,2}\s?[Ee]\d{1,3}|第\s?\d+\s?[集话話期]|\d{3,4}[pP]|[46][kK]|BluRay|WEB-?DL|HDTV|REMUX|HDR|"
    r"\[[^\]]*\]|【[^】]*】")


def enabled() -> bool:
    return bool(db.get_setting("tmdb_enabled", False)) and bool(
        str(db.get_setting("tmdb_api_key", "") or "").strip())


def _clean_query(title: str) -> str:
    """去掉集数/画质等噪声，取 TMDB 搜索用的片名。"""
    s = _NOISE_RE.sub(" ", title or "")
    # 截掉年份及其后的内容（官方名一般不含年份）
    m = re.search(r"[（(]\s*(19|20)\d{2}\s*[）)]", s)
    if m:
        s = s[:m.start()]
    return re.sub(r"\s+", " ", s).strip(" -_.·|")[:80]


def _pick_year(item: dict) -> str:
    date = (item.get("first_air_date") or item.get("release_date") or "")
    return date[:4] if re.match(r"^(19|20)\d{2}", date) else ""


def refine(title: str, category: str) -> dict | None:
    """查询 TMDB，返回 {name, year, kind}；未启用/未命中返回 None。"""
    if not enabled():
        return None
    query = _clean_query(title)
    if len(query) < 2:
        return None
    kind = "tv" if category in _TV_CATS else "movie"
    key = f"{kind}:{query.lower()}"
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now < cached[0]:
            return cached[1]
    result = _search(query, kind)
    with _CACHE_LOCK:
        _CACHE[key] = (now + _CACHE_TTL, result)
        if len(_CACHE) > 1000:
            for k in list(_CACHE)[:200]:
                _CACHE.pop(k, None)
    return result


def _search(query: str, kind: str) -> dict | None:
    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    lang = str(db.get_setting("tmdb_language", "zh-CN") or "zh-CN") or "zh-CN"
    try:
        from ..worker import jobs as _jobs
        proxy = _jobs._tg_proxy()
    except Exception:
        proxy = None
    try:
        resp = httpx.get(
            f"https://api.themoviedb.org/3/search/{kind}",
            params={"api_key": api_key, "query": query, "language": lang, "page": 1},
            proxy=proxy, timeout=10,
        )
        if resp.status_code != 200:
            return None
        results = resp.json().get("results") or []
        if not results:
            return None
        item = results[0]
        name = item.get("name") or item.get("title") or ""
        year = _pick_year(item)
        if not name:
            return None
        return {"name": name, "year": year, "kind": kind}
    except Exception:
        return None


def display_name(title: str, category: str) -> str | None:
    """官方名 (年份)；拿不到年份时只回官方名。用于通知标题与子目录命名。"""
    info = refine(title, category)
    if not info:
        return None
    return f"{info['name']} ({info['year']})" if info["year"] else info["name"]
