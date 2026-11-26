"""TMDB 选剧浏览、搜索与海报代理路由。"""
import datetime
import re
import threading
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ... import auth, database as db
from ...core import logstream
from ...worker import jobs
from ..deps import AUTH_COOKIE, require_user
from ._common import POSTER_COOKIE

router = APIRouter()

_TMDB_IMG_BASE = "https://image.tmdb.org/t/p"

# 地区分组映射：前端传简码，后端同时适配 TMDB 剧集 origin_country 与电影筛选
_REGION_COUNTRIES = {
    "CN": "CN|HK|TW",
    "JPKR": "JP|KR",
    "USGB": "US|GB",
}
_REGION_LANGS = {
    "CN": {"zh", "cn"},
    "JPKR": {"ja", "ko"},
    "USGB": {"en"},
}
# 常规追剧默认屏蔽：新闻(10763)、真人秀(10764)、肥皂剧(10766)、脱口秀(10767)
_NOISE_TV_GENRES = "10763,10764,10766,10767"


def _tmdb_lang() -> str:
    lang = str(db.get_setting("tmdb_language", "zh-CN") or "zh-CN").strip()
    return lang if lang else "zh-CN"


def _tmdb_get(path: str, params: dict, timeout: float = 10):
    """带代理请求 TMDB，统一走 core.tmdb_scraper.tmdb_get（代理优先、失败直连）。"""
    from ...core.tmdb_scraper import tmdb_get
    return tmdb_get(path, params, timeout=timeout)


def _tmdb_item(it: dict, kind: str) -> dict | None:
    """把 TMDB 的 tv/movie 条目统一成订阅页卡片数据。"""
    name = it.get("name") or it.get("title") or ""
    if not name:
        return None
    date = it.get("first_air_date") or it.get("release_date") or ""
    poster_path = it.get("poster_path") or ""
    oc = [str(c).upper() for c in (it.get("origin_country") or []) if c]
    return {
        "id": it.get("id"),
        "kind": kind,
        "name": name,
        "original": it.get("original_name") or it.get("original_title") or "",
        "year": date[:4] if re.match(r"^(19|20)\d{2}", date) else "",
        "date": date,
        "overview": (it.get("overview") or "").strip()[:200],
        "rating": round(float(it.get("vote_average") or 0), 1),
        "poster": f"{_TMDB_IMG_BASE}/w200{poster_path}" if poster_path else "",
        "poster_path": poster_path,
        "popularity": float(it.get("popularity") or 0),
        "origin_country": oc,
        "original_language": str(it.get("original_language") or "").lower(),
        "episode_count": 0,
    }


def _match_region(it: dict, region: str) -> bool:
    """用于 trending / search 等不原生支持 with_origin_country 的接口做地区过滤。"""
    if not region:
        return True
    targets = set(_REGION_COUNTRIES.get(region, region).split("|"))
    oc = set(it.get("origin_country") or [])
    if oc & targets:
        return True
    langs = _REGION_LANGS.get(region)
    if langs and not oc:
        return (it.get("original_language") or "") in langs
    # 电影 trending 条目通常无 origin_country，回退到 original_language
    if it.get("kind") == "movie" and langs:
        return (it.get("original_language") or "") in langs
    return False


# TV 详情（最新季 + 集数 + 产地）缓存：带 TTL 与容量上限
_TV_SEASON_CACHE: dict[tuple[str, int], tuple[float, tuple[int, str, int, list[str]] | None]] = {}
_TV_SEASON_LOCK = threading.Lock()
_TV_SEASON_TTL = 86400.0
_TV_SEASON_FAIL_TTL = 300.0
_TV_SEASON_MAX = 500

_DISCOVER_CACHE: dict[tuple, tuple[float, dict]] = {}
_DISCOVER_LOCK = threading.Lock()
_DISCOVER_TTL = 300.0
_DISCOVER_MAX = 100


def _latest_season_of(tv_id, api_key: str, lang: str) -> tuple[int, str, int, list[str]] | None:
    """取剧集最新一季的 (季号, 播出日期, 本季集数, 产地列表)。"""
    try:
        tv_id = int(tv_id)
    except (TypeError, ValueError):
        return None
    key = (lang, tv_id)
    now = time.time()
    with _TV_SEASON_LOCK:
        hit = _TV_SEASON_CACHE.get(key)
        if hit and now < hit[0]:
            return hit[1]
        if len(_TV_SEASON_CACHE) > _TV_SEASON_MAX:
            _TV_SEASON_CACHE.clear()
    best = None
    ok = False
    try:
        resp = _tmdb_get(f"/tv/{tv_id}", {"api_key": api_key, "language": lang})
        if resp.status_code == 200:
            ok = True
            data = resp.json()
            oc = [str(c).upper() for c in (data.get("origin_country") or []) if c]
            for s in (data.get("seasons") or []):
                try:
                    sn = int(s.get("season_number") or 0)
                    ep_cnt = int(s.get("episode_count") or 0)
                except (TypeError, ValueError):
                    continue
                ad = str(s.get("air_date") or "").strip()
                if sn >= 1 and ad and (best is None or sn > best[0]):
                    best = (sn, ad, ep_cnt, oc)
        else:
            logstream.warn("system", f"TMDB /tv/{tv_id} 返回 {resp.status_code}")
    except Exception as e:
        logstream.warn("system", f"TMDB /tv/{tv_id} 请求失败: {logstream.exc(e)}")
    with _TV_SEASON_LOCK:
        ttl = _TV_SEASON_TTL if (ok and best) else _TV_SEASON_FAIL_TTL
        _TV_SEASON_CACHE[key] = (time.time() + ttl, best)
    return best


def _enrich_tv_latest_season(items: list[dict], api_key: str, lang: str) -> list[dict]:
    """把 TV 条目的播出时间替换为最新季的，并附上季号、集数与产地标签。"""
    tv_items = [it for it in items if it.get("kind") == "tv"]
    if not tv_items:
        return items
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        infos = list(ex.map(lambda it: _latest_season_of(it.get("id"), api_key, lang), tv_items))
    for it, info in zip(tv_items, infos):
        if info:
            it["date"] = info[1]
            it["season"] = f"S{info[0]:02d}"
            if info[2] > 0:
                it["episode_count"] = info[2]
            if info[3] and not it.get("origin_country"):
                it["origin_country"] = info[3]
    return items


@router.get("/tmdb/discover")
def tmdb_discover(kind: str = "trending", page: int = 1, region: str = "",
                  _=Depends(require_user)):
    """追剧选剧分类浏览：按热门/在播/待映/新番/综艺/纪录片/高分组织，支持地区筛选。"""
    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        raise HTTPException(400, "请先在「订阅追剧」页填写并保存 TMDB API Key")
    page = min(max(page, 1), 500)
    today = datetime.date.today()
    today_s = today.isoformat()
    hi_s = (today + datetime.timedelta(days=90)).isoformat()
    lang = _tmdb_lang()
    region = str(region or "").strip().upper()
    if region and region not in _REGION_COUNTRIES and not re.fullmatch(r"[A-Z]{2}", region):
        raise HTTPException(400, "region 需为 CN / JPKR / USGB 或两位国家代码")

    cache_key = (kind, page, region, lang, api_key)
    now = time.time()
    with _DISCOVER_LOCK:
        hit = _DISCOVER_CACHE.get(cache_key)
        if hit and now < hit[0]:
            return hit[1]

    origin_expr = _REGION_COUNTRIES.get(region, region)
    movie_base = {"api_key": api_key, "language": lang, "page": page, "include_adult": "false"}
    tv_base = {"api_key": api_key, "language": lang, "page": page, "include_adult": "false"}
    if origin_expr:
        movie_base["with_origin_country"] = origin_expr
        tv_base["with_origin_country"] = origin_expr

    items: list[dict] = []
    total_pages = 1

    def _collect(path: str, params: dict, fixed_kind: str = "") -> None:
        nonlocal total_pages
        try:
            resp = _tmdb_get(path, params)
        except Exception as e:
            logstream.warn("system", f"TMDB {path} 请求失败: {logstream.exc(e)}")
            return
        if resp.status_code != 200:
            logstream.warn("system", f"TMDB {path} 返回 {resp.status_code}: {str(resp.text)[:120]}")
            return
        data = resp.json()
        total_pages = max(total_pages, min(int(data.get("total_pages") or 1), 500))
        for raw in (data.get("results") or []):
            k = fixed_kind or raw.get("media_type") or ""
            if k not in ("tv", "movie"):
                continue
            item = _tmdb_item(raw, k)
            if item:
                items.append(item)

    if kind == "trending":
        _collect("/trending/all/week", {"api_key": api_key, "language": lang, "page": page})
        if region:
            items = [it for it in items if _match_region(it, region)]
    elif kind == "on_air":
        # 剧集：近期在播且屏蔽脱口秀/新闻/肥皂剧/真人秀，并设最低评价门槛防冷门水片
        _collect("/discover/tv", {
            **tv_base, "sort_by": "popularity.desc",
            "air_date.gte": (today - datetime.timedelta(days=10)).isoformat(),
            "air_date.lte": (today + datetime.timedelta(days=7)).isoformat(),
            "without_genres": _NOISE_TV_GENRES,
            "vote_count.gte": "10",
        }, "tv")
        _collect("/discover/movie", {
            **movie_base, "sort_by": "popularity.desc",
            "primary_release_date.gte": (today - datetime.timedelta(days=60)).isoformat(),
            "primary_release_date.lte": today_s,
            "vote_count.gte": "20",
        }, "movie")
    elif kind == "upcoming":
        _collect("/discover/movie", {
            **movie_base, "sort_by": "popularity.desc",
            "primary_release_date.gte": today_s,
            "primary_release_date.lte": hi_s,
        }, "movie")
        _collect("/discover/tv", {
            **tv_base, "sort_by": "first_air_date.asc",
            "first_air_date.gte": today_s,
            "first_air_date.lte": hi_s,
            "without_genres": _NOISE_TV_GENRES,
        }, "tv")
        _collect("/discover/tv", {
            **tv_base, "sort_by": "popularity.desc",
            "first_air_date.lte": today_s,
            "without_genres": _NOISE_TV_GENRES,
            "vote_count.gte": "100",
        }, "tv")
    elif kind == "anime":
        # 未指定地区时默认聚焦日漫/国漫，避免欧美长寿卡通霸榜；同时覆盖动画电影
        anime_origin = origin_expr or "JP|CN"
        _collect("/discover/tv", {
            **tv_base, "with_origin_country": anime_origin, "sort_by": "popularity.desc",
            "with_genres": "16",
            "air_date.gte": (today - datetime.timedelta(days=45)).isoformat(),
        }, "tv")
        _collect("/discover/movie", {
            **movie_base, "with_origin_country": anime_origin, "sort_by": "popularity.desc",
            "with_genres": "16",
            "primary_release_date.gte": (today - datetime.timedelta(days=120)).isoformat(),
            "vote_count.gte": "15",
        }, "movie")
    elif kind == "variety":
        _collect("/discover/tv", {
            **tv_base, "sort_by": "popularity.desc",
            "with_genres": "10764|10767",
            "air_date.gte": (today - datetime.timedelta(days=60)).isoformat(),
            "vote_count.gte": "5",
        }, "tv")
    elif kind == "doc":
        _collect("/discover/tv", {
            **tv_base, "sort_by": "popularity.desc",
            "with_genres": "99",
            "without_genres": _NOISE_TV_GENRES,
            "air_date.gte": (today - datetime.timedelta(days=120)).isoformat(),
            "vote_count.gte": "10",
        }, "tv")
        _collect("/discover/movie", {
            **movie_base, "sort_by": "popularity.desc",
            "with_genres": "99",
            "primary_release_date.gte": (today - datetime.timedelta(days=180)).isoformat(),
            "vote_count.gte": "15",
        }, "movie")
    elif kind == "top_rated":
        _collect("/discover/tv", {
            **tv_base, "sort_by": "vote_average.desc",
            "without_genres": _NOISE_TV_GENRES,
            "vote_average.gte": "7.8", "vote_count.gte": "300",
        }, "tv")
        _collect("/discover/movie", {
            **movie_base, "sort_by": "vote_average.desc",
            "vote_average.gte": "7.8", "vote_count.gte": "500",
        }, "movie")
    else:
        raise HTTPException(400, f"未知分类: {kind}")

    items = _enrich_tv_latest_season(items, api_key, lang)
    if kind == "upcoming":
        keep = []
        for it in items:
            d = str(it.get("date") or "")
            if it.get("kind") == "movie":
                if d >= today_s:
                    keep.append(it)
            elif today_s <= d <= hi_s:
                keep.append(it)
        items = keep
        items.sort(key=lambda x: x.get("date") or "9999-12-31")
    elif kind == "top_rated":
        items.sort(key=lambda x: (-x["rating"], -x["popularity"]))
    elif kind != "trending":
        items.sort(key=lambda x: -x["popularity"])

    seen, uniq = set(), []
    for it in items:
        key = (it.get("kind"), it.get("id"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    items = uniq
    if not items and page == 1 and not region:
        raise HTTPException(502, "TMDB 暂未返回可展示的条目，请稍后重试或检查代理设置")

    result = {"items": items[:40], "page": page, "total_pages": total_pages}
    with _DISCOVER_LOCK:
        if len(_DISCOVER_CACHE) > _DISCOVER_MAX:
            _DISCOVER_CACHE.clear()
        _DISCOVER_CACHE[cache_key] = (time.time() + _DISCOVER_TTL, result)
    return result


@router.get("/tmdb/search")
def tmdb_search(query: str = "", kind: str = "all", page: int = 1,
                year: str = "", _=Depends(require_user)):
    """按名称、年份或 IMDb ID（tt...）搜索 TMDB 影视，支持分页。"""
    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        raise HTTPException(400, "请先在「订阅追剧」页填写并保存 TMDB API Key")
    query = query.strip()
    if len(query) < 2:
        raise HTTPException(400, "搜索词至少 2 个字符")
    page = min(max(page, 1), 500)
    year = str(year or "").strip()
    if year and not re.fullmatch(r"(19|20)\d{2}", year):
        raise HTTPException(400, "年份需为 4 位数字")
    lang = _tmdb_lang()

    items: list[dict] = []
    total_pages = 1

    # 支持直接输入 IMDb ID（如 tt1234567）精确查找
    if re.fullmatch(r"tt\d{5,}", query, re.I):
        try:
            resp = _tmdb_get(f"/find/{query}", {"api_key": api_key, "language": lang, "external_source": "imdb_id"})
            if resp.status_code == 200:
                data = resp.json()
                for k, field in (("tv", "tv_results"), ("movie", "movie_results")):
                    items += [x for x in (_tmdb_item(it, k) for it in (data.get(field) or [])) if x]
        except Exception as e:
            logstream.warn("system", f"TMDB /find/{query} 请求失败: {logstream.exc(e)}")
    else:
        kinds = ("tv", "movie") if kind not in ("tv", "movie") else (kind,)
        for k in kinds:
            params = {"api_key": api_key, "query": query, "language": lang, "page": page, "include_adult": "false"}
            if year:
                params["first_air_date_year" if k == "tv" else "year"] = year
            try:
                resp = _tmdb_get(f"/search/{k}", params)
                if resp.status_code != 200:
                    logstream.warn("system", f"TMDB /search/{k} 返回 {resp.status_code}: {str(resp.text)[:120]}")
                    continue
                data = resp.json()
                total_pages = max(total_pages, min(int(data.get("total_pages") or 1), 500))
                items += [x for x in (_tmdb_item(it, k) for it in (data.get("results") or [])) if x]
            except Exception as e:
                logstream.warn("system", f"TMDB /search/{k} 请求失败: {logstream.exc(e)}")
                continue

    seen, uniq = set(), []
    for it in items:
        key = (it.get("kind"), it.get("id"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    items = uniq
    items.sort(key=lambda x: -x["popularity"])
    items = _enrich_tv_latest_season(items, api_key, lang)
    return {"items": items[:24], "page": page, "total_pages": total_pages}


_POSTER_CACHE: dict[str, tuple[bytes, str]] = {}
_POSTER_CACHE_LOCK = threading.Lock()
_POSTER_CACHE_MAX = 300


@router.get("/tmdb/poster")
def tmdb_poster(
    request: Request,
    path: str = "",
    authorization: str = Header(default=""),
):
    """后端代理 TMDB 海报图片（Bearer / 登录态 Cookie / 海报专用 Cookie 三重鉴权 + 内存 LRU 缓存）。"""
    from fastapi.responses import Response
    user = ""
    if authorization.startswith("Bearer "):
        user = auth.check_token(authorization[7:]) or ""
    if not user:
        user = auth.check_token(request.cookies.get(AUTH_COOKIE, "")) or ""
    if not user:
        user = auth.check_token(request.cookies.get(POSTER_COOKIE, "")) or ""
    if not user:
        raise HTTPException(401, "登录已过期，请重新登录")

    clean_path = path if path.startswith("/") else f"/{path}"
    if not re.match(r"^/[A-Za-z0-9._-]+(\.jpg|\.png|\.jpeg|\.webp)$", clean_path):
        raise HTTPException(400, "海报路径不合法")

    with _POSTER_CACHE_LOCK:
        cached = _POSTER_CACHE.get(clean_path)
    if cached is not None:
        content, media_type = cached
        return Response(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=2592000, immutable", "X-Poster-Cache": "HIT"},
        )

    proxy = None
    try:
        proxy = jobs._tg_proxy()
    except Exception:
        pass

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    url = f"{_TMDB_IMG_BASE}/w200{clean_path}"
    resp = None
    import httpx

    try:
        resp = httpx.get(url, headers=headers, proxy=proxy, timeout=12)
        if resp.status_code != 200 and proxy:
            resp = httpx.get(url, headers=headers, proxy=None, timeout=12)
    except Exception as e:
        if proxy:
            try:
                resp = httpx.get(url, headers=headers, proxy=None, timeout=12)
            except Exception as e2:
                logstream.warn("scrape", f"海报拉取失败（直连兜底亦失败）: {logstream.exc(e2)}")
                raise HTTPException(502, "海报获取失败（网络异常，已尝试代理与直连）")
        else:
            logstream.warn("scrape", f"海报拉取失败: {logstream.exc(e)}")
            raise HTTPException(502, "海报获取失败（网络异常）")

    if not resp or resp.status_code != 200:
        raise HTTPException(resp.status_code if resp else 404, "海报获取失败")
    ct = (resp.headers.get("content-type") or "").lower()
    if not ct.startswith("image/"):
        raise HTTPException(404, "海报格式错误")
    if len(resp.content) > 6 * 1024 * 1024:
        raise HTTPException(404, "海报体积过大")

    media_type = resp.headers.get("content-type", "image/jpeg")
    with _POSTER_CACHE_LOCK:
        if len(_POSTER_CACHE) >= _POSTER_CACHE_MAX:
            for k in list(_POSTER_CACHE.keys())[:50]:
                _POSTER_CACHE.pop(k, None)
        _POSTER_CACHE[clean_path] = (resp.content, media_type)

    return Response(
        content=resp.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=2592000, immutable", "X-Poster-Cache": "MISS"},
    )
