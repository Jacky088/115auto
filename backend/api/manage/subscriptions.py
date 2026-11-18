"""订阅追剧与 TMDB 检索/海报代理路由。"""
import json
import re
import threading
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ... import auth, database as db
from ...core import logstream, subscribe as submod
from ...worker import jobs
from ..deps import AUTH_COOKIE, require_user
from ._common import POSTER_COOKIE

router = APIRouter()


# ---------- subscriptions ----------
def _clean_aliases(value) -> list[str]:
    """校验并归一化别名输入：接受字符串（换行/逗号分隔）或字符串数组。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,，;；]", value)
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise HTTPException(400, "别名需为字符串或字符串数组")
    out, seen = [], set()
    for part in parts:
        s = str(part or "").strip()
        if not s:
            continue
        if len(s) > submod.MAX_KEYWORD_LEN:
            raise HTTPException(400, f"别名「{s[:20]}…」超过 {submod.MAX_KEYWORD_LEN} 字符")
        n = submod.normalize(s)
        if n and n not in seen:
            seen.add(n)
            out.append(s)
    if len(out) > submod.MAX_ALIASES:
        raise HTTPException(400, f"别名最多 {submod.MAX_ALIASES} 个")
    return out


def _clean_min_quality(value) -> str:
    """订阅级最低画质：空串=跟随全局设置。"""
    s = str(value or "").strip().lower()
    if s not in ("", "720p", "1080p", "2160p"):
        raise HTTPException(400, "订阅最低画质仅支持 跟随全局 / 720p / 1080p / 2160p")
    return s


def _clean_tmdb_meta(body: dict, current_id=None, current_kind="") -> tuple[int | None, str]:
    """校验 TMDB 元数据（仅做记录展示，不参与匹配）。"""
    tmdb_id = current_id
    if "tmdb_id" in body:
        raw = body.get("tmdb_id")
        if raw in (None, ""):
            tmdb_id = None
        else:
            try:
                tmdb_id = int(raw)
            except (TypeError, ValueError):
                raise HTTPException(400, "tmdb_id 必须为整数")
            if tmdb_id <= 0:
                raise HTTPException(400, "tmdb_id 必须为正整数")
    kind = current_kind or ""
    if "kind" in body:
        kind = str(body.get("kind") or "").strip()
        if kind not in ("", "tv", "movie"):
            raise HTTPException(400, "kind 仅支持 tv / movie")
    return tmdb_id, kind


def _reset_progress_sql(sub_id: int):
    db.execute(
        "UPDATE subscriptions SET last_ep='', last_season=NULL, last_episode=NULL,"
        " last_title='', last_match_at=NULL WHERE id=?", (sub_id,))


@router.get("/subscriptions")
def list_subscriptions(_=Depends(require_user)):
    items = []
    for r in db.query("SELECT * FROM subscriptions ORDER BY id DESC"):
        row = dict(r)
        row["aliases"] = submod.aliases_of(row)  # 统一返回数组，前端好比对/展示
        items.append(row)
    return {"items": items,
            "subscription_only": bool(db.get_setting("subscription_only", False))}


@router.post("/subscriptions")
def add_subscription(body: dict, _=Depends(require_user)):
    kw = str(body.get("keyword") or "").strip()
    if not kw:
        raise HTTPException(400, "关键词不能为空")
    if len(kw) > submod.MAX_KEYWORD_LEN:
        raise HTTPException(400, f"关键词最长 {submod.MAX_KEYWORD_LEN} 字符")
    aliases = _clean_aliases(body.get("aliases"))
    conflict = submod.find_conflict(kw, aliases)
    if conflict:
        raise HTTPException(400, f"订阅「{kw}」与现有订阅「{conflict['keyword']}」重复")
    tmdb_id, kind = _clean_tmdb_meta(body)
    min_quality = _clean_min_quality(body.get("min_quality"))
    db.execute(
        "INSERT INTO subscriptions(keyword,note,aliases,tmdb_id,kind,min_quality,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (kw, (body.get("note") or "")[:200],
         json.dumps(aliases, ensure_ascii=False), tmdb_id, kind, min_quality, time.time()))
    logstream.info("subscribe", f"添加追剧订阅：{kw}（别名 {len(aliases)} 个）")
    return {"ok": True}


@router.put("/subscriptions/{sub_id}")
def update_subscription(sub_id: int, body: dict, _=Depends(require_user)):
    sub = db.query("SELECT * FROM subscriptions WHERE id=?", (sub_id,), one=True)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    row = dict(sub)
    if "enabled" in body:
        db.execute("UPDATE subscriptions SET enabled=? WHERE id=?",
                   (1 if body["enabled"] else 0, sub_id))

    new_kw = str(row.get("keyword") or "").strip()
    kw_changed = False
    if "keyword" in body:
        kw = str(body["keyword"] or "").strip()
        if not kw:
            raise HTTPException(400, "关键词不能为空")
        if len(kw) > submod.MAX_KEYWORD_LEN:
            raise HTTPException(400, f"关键词最长 {submod.MAX_KEYWORD_LEN} 字符")
        kw_changed = kw != new_kw
        new_kw = kw
    new_aliases = submod.aliases_of(row)
    aliases_changed = False
    if "aliases" in body:
        new_aliases = _clean_aliases(body.get("aliases"))
        aliases_changed = new_aliases != submod.aliases_of(row)
    if kw_changed or aliases_changed:
        conflict = submod.find_conflict(new_kw, new_aliases, exclude_id=sub_id)
        if conflict:
            raise HTTPException(400, f"订阅「{new_kw}」与现有订阅「{conflict['keyword']}」重复")
    if kw_changed:
        db.execute("UPDATE subscriptions SET keyword=? WHERE id=?", (new_kw, sub_id))
    if aliases_changed:
        db.execute("UPDATE subscriptions SET aliases=? WHERE id=?",
                   (json.dumps(new_aliases, ensure_ascii=False), sub_id))
    tmdb_id, kind = _clean_tmdb_meta(body, row.get("tmdb_id"), row.get("kind") or "")
    if "tmdb_id" in body:
        db.execute("UPDATE subscriptions SET tmdb_id=? WHERE id=?", (tmdb_id, sub_id))
    if "kind" in body:
        db.execute("UPDATE subscriptions SET kind=? WHERE id=?", (kind, sub_id))
    if "min_quality" in body:
        db.execute("UPDATE subscriptions SET min_quality=? WHERE id=?",
                   (_clean_min_quality(body.get("min_quality")), sub_id))
    # 关键词变更后旧剧的进度不再适用；reset_progress 显式重置也可
    if body.get("reset_progress") or kw_changed:
        _reset_progress_sql(sub_id)
    return {"ok": True}


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(sub_id: int, _=Depends(require_user)):
    row = db.query("SELECT keyword FROM subscriptions WHERE id=?", (sub_id,), one=True)
    db.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
    if row:
        logstream.info("subscribe", f"删除追剧订阅: {row['keyword']}")
    return {"ok": True}


# ---------- tmdb 选剧搜索（订阅页） ----------
_TMDB_IMG_BASE = "https://image.tmdb.org/t/p"


def _tmdb_lang() -> str:
    """TMDB 返回语言固定为简体中文（原先的 tmdb_language 设置无入口，已移除）。"""
    return "zh-CN"


def _tmdb_get(path: str, params: dict, timeout: float = 10):
    """带代理请求 TMDB，返回响应对象；调用方自行处理异常。

    统一走 core.tmdb_scraper.tmdb_get：与刮削/订阅共用「代理优先、失败直连」的同一套逻辑，
    避免两处客户端行为漂移（此前这里只取代理，代理不可用时直接抛错）。"""
    from ...core.tmdb_scraper import tmdb_get
    return tmdb_get(path, params, timeout=timeout)


def _tmdb_item(it: dict, kind: str) -> dict | None:
    """把 TMDB 的 tv/movie 条目统一成订阅页卡片数据。"""
    name = it.get("name") or it.get("title") or ""
    if not name:
        return None
    date = it.get("first_air_date") or it.get("release_date") or ""
    poster_path = it.get("poster_path") or ""
    return {
        "id": it.get("id"), "kind": kind, "name": name,
        "original": it.get("original_name") or it.get("original_title") or "",
        "year": date[:4] if re.match(r"^(19|20)\d{2}", date) else "",
        "date": date,
        "overview": (it.get("overview") or "").strip()[:200],
        "rating": round(float(it.get("vote_average") or 0), 1),
        "poster": f"{_TMDB_IMG_BASE}/w200{poster_path}" if poster_path else "",
        "poster_path": poster_path,
        "popularity": float(it.get("popularity") or 0),
    }


# TV 详情（季列表）缓存：季播剧的最新季基本不变，进程内缓存即可（带 TTL 与容量上限）
_TV_SEASON_CACHE: dict[tuple[str, int], tuple[float, tuple[int, str] | None]] = {}
_TV_SEASON_LOCK = threading.Lock()  # 同步接口跑在线程池，缓存读写需互斥
_TV_SEASON_TTL = 86400.0       # 成功且有数据：24 小时
_TV_SEASON_FAIL_TTL = 300.0    # 失败/无数据：5 分钟后重试，避免网络抖动被缓存一整天
_TV_SEASON_MAX = 500

# 分类浏览结果缓存：一次 discover 最多触发 3 个列表 + 数十个详情请求（N+1），
# 首屏反复切换分类会拖慢页面并消耗 TMDB 配额；短缓存 5 分钟，只缓存成功结果。
_DISCOVER_CACHE: dict[tuple, tuple[float, dict]] = {}
_DISCOVER_LOCK = threading.Lock()
_DISCOVER_TTL = 300.0
_DISCOVER_MAX = 100


def _latest_season_of(tv_id, api_key: str, lang: str) -> tuple[int, str] | None:
    """取剧集「最新一季」的季号与播出日期（季号≥1 且有 air_date 的最大季）。

    季播剧（如已经出到 S04 的长寿剧）在列表接口里只有 1999 年的首播日期，
    对追剧没有参考意义；这里用详情接口的 seasons 覆盖为最新季的数据。
    """
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
            _TV_SEASON_CACHE.clear()  # 简单兜底：条目超限时整体重来，避免无限增长
    best = None
    ok = False
    try:
        resp = _tmdb_get(f"/tv/{tv_id}", {"api_key": api_key, "language": lang})
        if resp.status_code == 200:
            ok = True
            for s in (resp.json().get("seasons") or []):
                try:
                    sn = int(s.get("season_number") or 0)
                except (TypeError, ValueError):
                    continue
                ad = str(s.get("air_date") or "").strip()
                if sn >= 1 and ad and (best is None or sn > best[0]):
                    best = (sn, ad)
        else:
            logstream.warn("system", f"TMDB /tv/{tv_id} 返回 {resp.status_code}")
    except Exception as e:
        logstream.warn("system", f"TMDB /tv/{tv_id} 请求失败: {logstream.exc(e)}")
    with _TV_SEASON_LOCK:
        ttl = _TV_SEASON_TTL if (ok and best) else _TV_SEASON_FAIL_TTL
        _TV_SEASON_CACHE[key] = (time.time() + ttl, best)
    return best


def _enrich_tv_latest_season(items: list[dict], api_key: str, lang: str) -> list[dict]:
    """把 TV 条目的播出时间替换为最新季的，并附上季号标签（S04）。"""
    tv_items = [it for it in items if it.get("kind") == "tv"]
    if not tv_items:
        return items
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        infos = list(ex.map(lambda it: _latest_season_of(it.get("id"), api_key, lang),
                            tv_items))
    for it, info in zip(tv_items, infos):
        if info:
            it["date"] = info[1]
            it["season"] = f"S{info[0]:02d}"
    return items


@router.get("/tmdb/discover")
def tmdb_discover(kind: str = "on_air", page: int = 1, region: str = "",
                  _=Depends(require_user)):
    """追剧选剧分类浏览：按「追剧日历」视角组织，电影与剧集各自对齐正确概念。

    kind:
      on_air  — 📺 正在播出：剧集=TMDB /tv/on_the_air（未来 7 天内有更新、在播）；
                电影=/movie/now_playing（正在影院热映）——电影没有「播出」概念
      upcoming— 📅 即将上线：电影=/movie/upcoming（已定档待映）；
                剧集=今天~未来 90 天内开播的新剧 + 最新一季定档的老剧（新一季），
                统一按 enrich 后的实际播出日期升序，保证日历顺序与展示日期一致
      anime   — 🎌 动漫新番（动画类目 + 最近 45 天内开播/更新的剧）
      doc     — 🌍 纪录片（纪录片类目 + 最近 90 天内开播/更新的剧）

    region: 可选两位地区码（如 CN/US），仅用于电影上映档期；不传则沿用 TMDB 默认。
    """
    import datetime

    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        raise HTTPException(400, "请先在「订阅追剧」页填写并保存 TMDB API Key")
    page = min(max(page, 1), 500)
    today = datetime.date.today()
    today_s = today.isoformat()
    hi_s = (today + datetime.timedelta(days=90)).isoformat()
    lang = _tmdb_lang()
    region = str(region or "").strip().upper()
    if region and not re.fullmatch(r"[A-Z]{2}", region):
        raise HTTPException(400, "region 需为两位国家/地区代码，如 CN / US")

    cache_key = (kind, page, region, lang, api_key)
    now = time.time()
    with _DISCOVER_LOCK:
        hit = _DISCOVER_CACHE.get(cache_key)
        if hit and now < hit[0]:
            return hit[1]

    movie_base = {"api_key": api_key, "language": lang, "page": page}
    if region:
        movie_base["region"] = region
    tv_base = {"api_key": api_key, "language": lang, "page": page,
               "include_adult": "false"}

    items: list[dict] = []
    total_pages = 1

    def _collect(path: str, params: dict, k: str) -> None:
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
        items.extend(x for x in (_tmdb_item(it, k)
                                 for it in (data.get("results") or [])) if x)

    if kind == "on_air":
        # 剧集：在更剧；电影：正在热映（两个世界的同一档位，保证类型筛选双方都有数据）
        _collect("/tv/on_the_air", dict(tv_base), "tv")
        _collect("/movie/now_playing", dict(movie_base), "movie")
    elif kind == "upcoming":
        # 电影：已定档待映
        _collect("/movie/upcoming", dict(movie_base), "movie")
        # 剧集①：系列首播在今天~90 天内的全新剧
        _collect("/discover/tv", {**tv_base, "sort_by": "first_air_date.asc",
                                  "first_air_date.gte": today_s,
                                  "first_air_date.lte": hi_s}, "tv")
        # 剧集②：老剧新一季。TMDB 的 first_air_date 是系列首播日，用它能拿到
        # 已播剧的最新一季日期；enrich 后再按「最新季播出日」过滤到 90 天窗口内
        _collect("/discover/tv", {**tv_base, "sort_by": "popularity.desc",
                                  "first_air_date.lte": today_s,
                                  "vote_count.gte": "100"}, "tv")
    elif kind == "anime":
        _collect("/discover/tv", {**tv_base, "sort_by": "popularity.desc",
                                  "with_genres": "16",
                                  "air_date.gte": (today - datetime.timedelta(days=45)).isoformat()},
                 "tv")
    elif kind == "doc":
        _collect("/discover/tv", {**tv_base, "sort_by": "popularity.desc",
                                  "with_genres": "99",
                                  "air_date.gte": (today - datetime.timedelta(days=90)).isoformat()},
                 "tv")
    else:
        raise HTTPException(400, f"未知分类: {kind}")
    if not items:
        raise HTTPException(502, "TMDB 请求失败，请检查 API Key 或网络/代理设置")

    # 先 enrich（可能改写 date），再排序：保证展示日期与排序依据一致
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
    else:
        items.sort(key=lambda x: -x["popularity"])

    # 新剧/回归剧两个来源可能同时返回同一部剧，按 (kind, id) 去重
    seen, uniq = set(), []
    for it in items:
        key = (it.get("kind"), it.get("id"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    items = uniq
    if not items:
        raise HTTPException(502, "TMDB 暂未返回可展示的条目，请稍后重试或检查代理设置")
    # 合并类目含电影+剧集两个来源，单页最多返回 40 条，保证类型筛选双方都有数据
    result = {"items": items[:40], "page": page, "total_pages": total_pages}
    with _DISCOVER_LOCK:
        if len(_DISCOVER_CACHE) > _DISCOVER_MAX:
            _DISCOVER_CACHE.clear()
        _DISCOVER_CACHE[cache_key] = (time.time() + _DISCOVER_TTL, result)
    return result


@router.get("/tmdb/search")
def tmdb_search(query: str = "", kind: str = "all", page: int = 1, _=Depends(require_user)):
    """按名称搜索 TMDB 影视（tv/movie），返回海报、年份、简介等供订阅选剧。"""
    api_key = str(db.get_setting("tmdb_api_key", "") or "").strip()
    if not api_key:
        raise HTTPException(400, "请先在「订阅追剧」页填写并保存 TMDB API Key")
    query = query.strip()
    if len(query) < 2:
        raise HTTPException(400, "搜索词至少 2 个字符")
    page = min(max(page, 1), 500)
    lang = _tmdb_lang()
    kinds = ("tv", "movie") if kind not in ("tv", "movie") else (kind,)
    items = []
    for k in kinds:
        try:
            resp = _tmdb_get(f"/search/{k}", {"api_key": api_key, "query": query,
                                              "language": lang, "page": page})
            if resp.status_code != 200:
                logstream.warn("system",
                               f"TMDB /search/{k} 返回 {resp.status_code}: {str(resp.text)[:120]}")
                continue
            items += [x for x in (_tmdb_item(it, k) for it in (resp.json().get("results") or [])) if x]
        except Exception as e:
            # 单类型失败不阻断另一类型，但必须留痕，便于定位 API Key/代理问题
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
    # TV 条目同样标注最新季（季号 + 最新季播出时间），与分类浏览口径一致
    items = _enrich_tv_latest_season(items, api_key, lang)
    return {"items": items[:24]}


# LRU 内存海报缓存，最多缓存 300 张海报（约 10~15MB 内存），避免重复并发请求 TMDB
_POSTER_CACHE: dict[str, tuple[bytes, str]] = {}
_POSTER_CACHE_MAX = 300


@router.get("/tmdb/poster")
def tmdb_poster(
    request: Request,
    path: str = "",
    authorization: str = Header(default=""),
):
    """后端代理 TMDB 海报图片（Bearer 头 / 登录态 Cookie / 海报专用 Cookie 三重鉴权，带内存缓存与海外直连保底）。

    不再接受 URL ?token= 参数：JWT 落在查询串会进入访问日志与浏览器历史。"""
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

    # 1. 命中内存缓存直接返回（0ms 响应，减轻网络与 TMDB 压力）
    if clean_path in _POSTER_CACHE:
        content, media_type = _POSTER_CACHE[clean_path]
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=2592000, immutable",
                "X-Poster-Cache": "HIT",
            },
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

    # 2. 拉取逻辑：若配置了代理，先尝试代理；代理失败/超时/报错时自动降级直连（海外 VPS 直连 TMDB 极快）
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

    # 3. 写入内存 LRU 缓存
    if len(_POSTER_CACHE) >= _POSTER_CACHE_MAX:
        for k in list(_POSTER_CACHE.keys())[:50]:
            _POSTER_CACHE.pop(k, None)
    _POSTER_CACHE[clean_path] = (resp.content, media_type)

    return Response(
        content=resp.content,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=2592000, immutable",
            "X-Poster-Cache": "MISS",
        },
    )
