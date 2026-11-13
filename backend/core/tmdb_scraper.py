"""TMDB 多阶段精确刮削引擎 & 本地 NFO/海报生成器（零 115 上传风控）。

流程：
1. 多阶段候选召回（P0 显式 tmdb_id → P1 片名+年份 → P2 无年份/简繁/副标题/英文原名/跨类型兜底）；
2. 多维加权校验打分（片名标准化相似度 45 + 年份与季播年对齐 25 + 媒体类型 15 + 季集规模物理对账 15 + 完整度 5）；
3. 置信度门槛 + 领先分差门禁（不满足则 0 秒跳过进入 `need_confirm`，绝不误改网盘）；
4. SQLite `tmdb_cache` 本地持久化缓存（避免重复打 TMDB，整部剧集共享同一校验结论）；
5. 权威元数据仅写入本地 `MEDIA_META_DIR`（`tvshow.nfo`/`movie.nfo`/`season.nfo`/`poster.jpg`/`fanart.jpg`）。
"""
from __future__ import annotations

import json
import random
import re
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from xml.dom import minidom

import httpx

from .. import config, database as db
from ..pan115.client import normalize_title
from . import logstream
from .media_parser import ParsedMedia, sanitize_filename
from .t2s import t2s

_TMDB_API_BASE = "https://api.themoviedb.org/3"
_TMDB_IMG_BASE = "https://image.tmdb.org/t/p"
_CACHE_DETAIL_TTL = 7 * 86400
_CACHE_SEARCH_TTL = 3 * 86400
_CACHE_NEGATIVE_TTL = 6 * 3600  # 空结果/请求失败的负缓存：故障期避免每轮扫描重复打 API


def _proxy() -> str | None:
    return str(db.get_setting("proxy", "") or "").strip() or None


def tmdb_get(path: str, params: dict, timeout: float = 12.0) -> httpx.Response:
    """请求 TMDB API：优先使用系统 TG 代理，若代理连接异常则降级直连保底。"""
    proxy = _proxy()
    url = f"{_TMDB_API_BASE}{path}"
    try:
        resp = httpx.get(url, params=params, proxy=proxy, timeout=timeout)
        if resp.status_code == 200 or not proxy:
            return resp
    except Exception:
        if not proxy:
            raise
    return httpx.get(url, params=params, proxy=None, timeout=timeout)


def tmdb_download_image(img_path: str, size: str = "w500", timeout: float = 15.0) -> bytes | None:
    """下载 TMDB 图片字节流（带代理回退）。"""
    clean = "/" + (img_path or "").lstrip("/")
    if clean == "/" or ".." in clean or not re.match(r"^/[A-Za-z0-9._-]+(\.jpg|\.png|\.jpeg|\.webp)$", clean):
        return None
    url = f"{_TMDB_IMG_BASE}/{size}{clean}"
    proxy = _proxy()
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "image/*,*/*;q=0.8"}
    try:
        r = httpx.get(url, headers=headers, proxy=proxy, timeout=timeout)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    if proxy:
        try:
            r = httpx.get(url, headers=headers, proxy=None, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r.content
        except Exception:
            pass
    return None


def _cache_get(key: str) -> dict | list | None:
    now = time.time()
    row = db.query("SELECT payload, expires_at FROM tmdb_cache WHERE cache_key=?", (key,), one=True)
    if not row:
        return None
    if float(row["expires_at"] or 0) < now:
        return None
    try:
        return json.loads(row["payload"])
    except Exception:
        return None


def _cache_set(key: str, payload: dict | list, ttl: int) -> None:
    now = time.time()
    raw = json.dumps(payload, ensure_ascii=False)
    db.execute(
        "INSERT OR REPLACE INTO tmdb_cache(cache_key, payload, expires_at, updated_at) VALUES(?,?,?,?)",
        (key, raw, now + ttl, now),
    )
    # 概率性清理过期缓存：表只增不删会无限膨胀（此前仅导出快照时裁剪）
    if random.random() < 0.02:
        try:
            db.execute("DELETE FROM tmdb_cache WHERE expires_at < ?", (now,))
        except Exception:
            pass


def _search_tmdb(kind: str, query: str, year: str, api_key: str) -> list[dict]:
    """执行一次带持久化缓存的 TMDB 搜索。"""
    q = (query or "").strip()
    if not q or len(q) < 1 or not api_key:
        return []
    yr = (year or "").strip()
    ck = f"search:{kind}:{q.lower()}:{yr}"
    cached = _cache_get(ck)
    if isinstance(cached, list):
        return cached

    params = {"api_key": api_key, "query": q, "language": "zh-CN", "include_adult": "false", "page": 1}
    if yr and re.fullmatch(r"(19|20)\d{2}", yr):
        params["year" if kind == "movie" else "first_air_date_year"] = yr
    try:
        resp = tmdb_get(f"/search/{kind}", params)
        if resp.status_code != 200:
            logstream.warn("scrape", f"TMDB /search/{kind}({q}) 返回 {resp.status_code}")
            # 空结果写短 TTL 负缓存：TMDB 故障/无结果时避免每轮扫描重复打 API
            _cache_set(ck, [], _CACHE_NEGATIVE_TTL)
            return []
        results = resp.json().get("results") or []
    except Exception as e:
        logstream.warn("scrape", f"TMDB /search/{kind}({q}) 请求异常: {e}")
        _cache_set(ck, [], _CACHE_NEGATIVE_TTL)
        return []

    if results:
        _cache_set(ck, results[:15], _CACHE_SEARCH_TTL)
    else:
        _cache_set(ck, [], _CACHE_NEGATIVE_TTL)
    return results[:15]


def get_tmdb_detail(kind: str, tmdb_id: int, api_key: str) -> dict | None:
    """获取并缓存 TMDB 条目完整详情（含多语言译名、别名、演职员、季信息）。"""
    if not tmdb_id or not api_key:
        return None
    ck = f"detail:{kind}:{int(tmdb_id)}"
    cached = _cache_get(ck)
    if isinstance(cached, dict) and cached.get("id"):
        return cached
    try:
        resp = tmdb_get(
            f"/{kind}/{int(tmdb_id)}",
            {
                "api_key": api_key,
                "language": "zh-CN",
                "append_to_response": "alternative_titles,translations,credits,external_ids",
            },
        )
        if resp.status_code != 200:
            logstream.warn("scrape", f"TMDB /{kind}/{tmdb_id} 返回 {resp.status_code}")
            return None
        data = resp.json()
    except Exception as e:
        logstream.warn("scrape", f"TMDB /{kind}/{tmdb_id} 请求失败: {e}")
        return None
    if isinstance(data, dict) and data.get("id"):
        _cache_set(ck, data, _CACHE_DETAIL_TTL)
        return data
    return None


def get_tv_season_episodes(tv_id: int, season: int, api_key: str) -> list[dict]:
    """拉取并缓存剧集某一季的单集列表（含单集中文名、首播日期、简介）。"""
    if not tv_id or season is None or not api_key:
        return []
    ck = f"season:{int(tv_id)}:{int(season)}"
    cached = _cache_get(ck)
    if isinstance(cached, list):
        return cached
    try:
        resp = tmdb_get(
            f"/tv/{int(tv_id)}/season/{int(season)}",
            {"api_key": api_key, "language": "zh-CN"},
        )
        if resp.status_code != 200:
            return []
        eps = resp.json().get("episodes") or []
    except Exception:
        return []
    if eps:
        _cache_set(ck, eps, _CACHE_DETAIL_TTL)
    return eps


def _extract_candidate_titles(detail: dict, kind: str) -> list[str]:
    """汇总候选条目的所有可信片名（简中主名、原名、港台/大陆/英文别称、translations）。"""
    names: list[str] = []
    for k in ("name", "title", "original_name", "original_title"):
        v = str(detail.get(k) or "").strip()
        if v:
            names.append(v)

    alt_block = detail.get("alternative_titles") or {}
    alt_list = alt_block.get("results" if kind == "tv" else "titles") or []
    for item in alt_list:
        iso = str(item.get("iso_3166_1") or "").upper()
        if iso in ("CN", "TW", "HK", "US", "JP", "GB", "KR", ""):
            t = str(item.get("title") or "").strip()
            if t:
                names.append(t)

    trans_block = detail.get("translations") or {}
    for tr in trans_block.get("translations") or []:
        iso639 = str(tr.get("iso_639_1") or "").lower()
        iso3166 = str(tr.get("iso_3166_1") or "").upper()
        if iso639 == "zh" or (iso639 == "en" and iso3166 == "US"):
            td = tr.get("data") or {}
            t = str(td.get("name") or td.get("title") or "").strip()
            if t:
                names.append(t)

    return list(dict.fromkeys(names))


def _title_similarity(query: str, cand_names: list[str]) -> tuple[float, str]:
    """计算待匹配片名与候选多语种片名列表之间的最高标准化相似度（0~1）。"""
    q_norm = normalize_title(query, fuzzy_t2s=True)
    q_base = normalize_title(re.split(r"[：:]", query, maxsplit=1)[0], fuzzy_t2s=True)
    if not q_norm:
        return 0.0, ""
    best = 0.0
    matched_name = ""
    for cn in cand_names:
        c_norm = normalize_title(cn, fuzzy_t2s=True)
        if not c_norm:
            continue
        if q_norm == c_norm:
            return 1.0, cn
        c_base = normalize_title(re.split(r"[：:]", cn, maxsplit=1)[0], fuzzy_t2s=True)
        if q_base and c_base and q_base == c_base and len(q_base) >= 2:
            score = 0.90
        elif len(q_norm) >= 2 and (q_norm in c_norm or c_norm in q_norm):
            ratio_len = min(len(q_norm), len(c_norm)) / max(len(q_norm), len(c_norm))
            score = 0.72 + 0.22 * ratio_len
        else:
            score = SequenceMatcher(None, q_norm, c_norm).ratio()
        if score > best:
            best = score
            matched_name = cn
    return best, matched_name


def remap_absolute_episode(
    season: int | None,
    episode: int | None,
    episode_end: int | None,
    detail: dict | None,
) -> tuple[int, int | None, int | None]:
    """将跨季连载绝对集号（如 E13~E24）按 TMDB 各常规季集数顺序重映射为 (S02, E01~)。

    仅当未显式指定高于第 1 季的季号（season 为 None 或 1）、集号超过第 1 季集数、
    且 TMDB 存在多季且累计总集数覆盖当前集号时启用，避免误拆单季超长番剧。
    """
    if episode is None or episode <= 0:
        return (1 if season is None else season), episode, episode_end
    if season is not None and season != 1:
        return season, episode, episode_end

    seasons_raw = (detail or {}).get("seasons") or []
    reg_seasons = sorted(
        (
            (int(s.get("season_number") or 0), int(s.get("episode_count") or 0))
            for s in seasons_raw
            if int(s.get("season_number") or 0) >= 1 and int(s.get("episode_count") or 0) > 0
        ),
        key=lambda x: x[0],
    )
    if len(reg_seasons) <= 1 or episode <= reg_seasons[0][1]:
        return (1 if season is None else season), episode, episode_end
    total_cnt = sum(cnt for _, cnt in reg_seasons)
    if episode > total_cnt:
        return (1 if season is None else season), episode, episode_end

    rem = episode
    span = (episode_end - episode) if (episode_end is not None and episode_end >= episode) else 0
    for s_num, s_cnt in reg_seasons:
        if rem <= s_cnt:
            new_end = (rem + span) if episode_end is not None else None
            return s_num, rem, new_end
        rem -= s_cnt
    return (1 if season is None else season), episode, episode_end


def _year_of(date_str: str) -> int | None:
    m = re.match(r"^(19|20)\d{2}", str(date_str or "").strip())
    return int(m.group(0)) if m else None


def score_candidate(
    detail: dict,
    kind: str,
    parsed: ParsedMedia,
    ep_stats: dict | None = None,
) -> dict:
    """多维加权校验打分（总分 0~100）：片名(45) + 年份/季播年(25) + 类型(15) + 季集结构对账(15) + 完整度(5)。"""
    cand_names = _extract_candidate_titles(detail, kind)
    reasons: list[str] = []

    # 1. 片名相似度（0 ~ 45，差于 0.55 倒扣）
    sim1, hit1 = _title_similarity(parsed.title, cand_names)
    sim2, hit2 = _title_similarity(parsed.alt_title, cand_names) if parsed.alt_title else (0.0, "")
    sim = max(sim1, sim2)
    if sim >= 0.99:
        title_pts = 45.0
        reasons.append(f"片名精确命中({hit1 or hit2})")
    elif sim >= 0.85:
        title_pts = 38.0 + (sim - 0.85) * 40.0
        reasons.append(f"片名高相似({sim:.0%})")
    elif sim >= 0.60:
        title_pts = 20.0 + (sim - 0.60) * 60.0
        reasons.append(f"片名部分匹配({sim:.0%})")
    else:
        title_pts = sim * 20.0 - 15.0
        reasons.append(f"片名差异较大({sim:.0%})")

    # 2. 年份核验（+25 / -30，TV 同步比对各季 air_date）
    main_date = detail.get("first_air_date") if kind == "tv" else detail.get("release_date")
    main_yr = _year_of(main_date or "")
    target_yr = _year_of(parsed.year)
    season_years: dict[int, int] = {}
    seasons_raw = detail.get("seasons") or []
    for s in seasons_raw:
        sn = int(s.get("season_number") or 0)
        sy = _year_of(s.get("air_date") or "")
        if sy:
            season_years[sn] = sy

    year_pts = 10.0  # 本地未携带年份时给中性基线分
    if target_yr is not None:
        compare_years = [y for y in ([main_yr] + list(season_years.values())) if y]
        if kind == "tv" and parsed.season is not None and parsed.season in season_years:
            cand_yr = season_years[parsed.season]
            delta = abs(cand_yr - target_yr)
        elif compare_years:
            delta = min(abs(y - target_yr) for y in compare_years)
        else:
            delta = None

        if delta is None:
            year_pts = 5.0
        elif delta == 0:
            year_pts = 25.0
            reasons.append(f"年份精确对齐({target_yr})")
        elif delta == 1:
            year_pts = 16.0
            reasons.append(f"年份±1年({target_yr})")
        elif delta == 2:
            year_pts = 0.0
        else:
            year_pts = -30.0
            reasons.append(f"年份差{delta}年(重度扣分)")

    # 3. 媒体类型一致性（+15 / 错配惩罚）
    is_movie_kw = bool(re.search(r"(剧场版|电影版|movie)", parsed.orig_name or "", re.I))
    if parsed.media_type == kind:
        type_pts = 15.0
    elif is_movie_kw and kind == "movie":
        type_pts = 15.0
    elif ep_stats and ep_stats.get("video_count", 1) >= 2 and kind == "movie":
        type_pts = -20.0
        reasons.append("本地含多集视频但候选为电影")
    else:
        type_pts = -12.0
        reasons.append(f"类型({kind})与解析({parsed.media_type})不一致")

    # 4. 季/集数结构物理对账（0 ~ 15 / 异常扣分）
    struct_pts = 8.0
    if kind == "tv":
        season_num = parsed.season if parsed.season is not None else 1
        max_ep = int((ep_stats or {}).get("max_ep") or parsed.episode or 0)
        ep_count = int((ep_stats or {}).get("video_count") or (1 if parsed.episode else 0))
        season_info = next((s for s in seasons_raw if int(s.get("season_number") or 0) == season_num), None)
        if season_info is not None:
            tmdb_cnt = int(season_info.get("episode_count") or 0)
            if tmdb_cnt > 0 and max_ep > 0:
                if max_ep <= tmdb_cnt:
                    struct_pts = 15.0
                    reasons.append(f"S{season_num:02d}集数对账吻合({max_ep}/{tmdb_cnt})")
                elif max_ep <= tmdb_cnt + 2:
                    struct_pts = 10.0
                else:
                    cum_cnt = sum(
                        int(s.get("episode_count") or 0)
                        for s in seasons_raw
                        if int(s.get("season_number") or 0) >= 1 and int(s.get("episode_count") or 0) > 0
                    )
                    if season_num == 1 and cum_cnt > tmdb_cnt:
                        if max_ep <= cum_cnt:
                            struct_pts = 14.0
                            reasons.append(f"多季累计集数吻合({max_ep}/{cum_cnt}集)")
                        elif max_ep <= cum_cnt + 2:
                            struct_pts = 9.0
                        else:
                            struct_pts = -10.0
                            reasons.append(f"S{season_num:02d}本地第{max_ep}集超出TMDB累计({cum_cnt}集)")
                    else:
                        struct_pts = -10.0
                        reasons.append(f"S{season_num:02d}本地第{max_ep}集超出TMDB({tmdb_cnt}集)")
            else:
                struct_pts = 12.0
        elif parsed.season is not None and seasons_raw:
            struct_pts = -8.0
            reasons.append(f"TMDB无第{season_num}季")
    else:
        if ep_stats and ep_stats.get("video_count", 1) == 1:
            struct_pts = 15.0

    # 5. 元数据完整度微调（0 ~ 5，避免纯人气吞掉正主）
    comp_pts = 0.0
    if detail.get("poster_path"):
        comp_pts += 2.0
    if (detail.get("overview") or "").strip():
        comp_pts += 1.5
    if int(detail.get("vote_count") or 0) >= 20:
        comp_pts += 1.5

    total = round(max(0.0, min(100.0, title_pts + year_pts + type_pts + struct_pts + comp_pts)), 1)
    title_display = str(detail.get("name") or detail.get("title") or "").strip()
    orig_display = str(detail.get("original_name") or detail.get("original_title") or "").strip()
    poster_path = str(detail.get("poster_path") or "").strip()
    backdrop_path = str(detail.get("backdrop_path") or "").strip()

    return {
        "tmdb_id": int(detail["id"]),
        "kind": kind,
        "title": title_display,
        "original_title": orig_display,
        "year": str(main_yr or ""),
        "date": str(main_date or ""),
        "poster_path": poster_path,
        "backdrop_path": backdrop_path,
        "overview": str(detail.get("overview") or "").strip()[:240],
        "rating": round(float(detail.get("vote_average") or 0), 1),
        "vote_count": int(detail.get("vote_count") or 0),
        "popularity": float(detail.get("popularity") or 0),
        "score": total,
        "reasons": reasons,
        "detail": detail,
    }


def recall_and_score(
    parsed: ParsedMedia,
    api_key: str,
    type_hint: str = "auto",
    ep_stats: dict | None = None,
) -> list[dict]:
    """多阶段 TMDB 召回与详情加权评分，返回按 score 降序排列的候选列表（最多 5 个）。"""
    if not api_key:
        return []

    pref_kind = type_hint if type_hint in ("tv", "movie") else ("tv" if parsed.media_type == "tv" else "movie")
    other_kind = "movie" if pref_kind == "tv" else "tv"

    # P0：目录/文件名/订阅已显式带 tmdb_id → 直连详查 100 分直接锁定
    if parsed.tmdb_id:
        for k in (pref_kind, other_kind):
            det = get_tmdb_detail(k, parsed.tmdb_id, api_key)
            if det:
                cand = score_candidate(det, k, parsed, ep_stats)
                cand["score"] = 100.0
                cand["reasons"] = ["显式 [tmdbid] 标签直接命中"] + cand["reasons"]
                return [cand]

    # P1 ~ P2：多级搜索召回并去重 (kind, tmdb_id)
    pool: dict[tuple[str, int], dict] = {}
    queries: list[tuple[str, str, str]] = []
    if parsed.title:
        if parsed.year:
            queries.append((pref_kind, parsed.title, parsed.year))
        queries.append((pref_kind, parsed.title, ""))
        s_title = t2s(parsed.title)
        if s_title != parsed.title:
            queries.append((pref_kind, s_title, ""))
        base_colon = re.split(r"[：:]", parsed.title, maxsplit=1)[0].strip()
        if base_colon and base_colon != parsed.title and len(base_colon) >= 2:
            queries.append((pref_kind, base_colon, parsed.year))
            queries.append((pref_kind, base_colon, ""))
    if parsed.alt_title:
        queries.append((pref_kind, parsed.alt_title, parsed.year))
        queries.append((pref_kind, parsed.alt_title, ""))

    # 跨类型兜底（防止剧场版 / 纪录片单部归类偏差）
    if type_hint == "auto" and parsed.title:
        queries.append((other_kind, parsed.title, parsed.year))
        queries.append((other_kind, parsed.title, ""))

    seen_q: set[tuple[str, str, str]] = set()
    for k, q, yr in queries:
        qn = (q or "").strip()
        if not qn:
            continue
        sig = (k, qn.lower(), yr)
        if sig in seen_q:
            continue
        seen_q.add(sig)
        hits = _search_tmdb(k, qn, yr, api_key)
        for h in hits[:6]:
            hid = h.get("id")
            if hid and (k, int(hid)) not in pool:
                pool[(k, int(hid))] = h
        if len(pool) >= 6 and yr and hits:
            # P1 精确搜出足够高相关候选时先快速进入打分，必要时才继续扩展
            top_name_sim, _ = _title_similarity(
                parsed.title or parsed.alt_title,
                [str(hits[0].get("name") or hits[0].get("title") or "")],
            )
            if top_name_sim >= 0.92:
                break

    if not pool:
        return []

    # 先粗排再对前 5 个拉完整详情精排（剧集季数/别名翻译验证）
    rough_list = []
    for (k, _), item in pool.items():
        nm = str(item.get("name") or item.get("title") or "")
        orig = str(item.get("original_name") or item.get("original_title") or "")
        sim, _ = _title_similarity(parsed.title or parsed.alt_title, [nm, orig])
        pop = float(item.get("popularity") or 0)
        rough_list.append((sim * 100 + min(pop, 50) * 0.1, k, item))
    rough_list.sort(key=lambda x: -x[0])

    scored: list[dict] = []
    for _, k, item in rough_list[:5]:
        det = get_tmdb_detail(k, int(item["id"]), api_key) or item
        scored.append(score_candidate(det, k, parsed, ep_stats))

    scored.sort(key=lambda x: (-x["score"], -x["vote_count"], -x["popularity"]))
    return scored


def decide_match(
    scored: list[dict],
    conf_threshold: float = 85.0,
    margin_threshold: float = 12.0,
) -> tuple[str, list[dict]]:
    """根据得分门槛与一二名分差决定 `auto` 自动锁定还是 `need_confirm` 挂起待人选。"""
    if not scored:
        return "need_confirm", []
    top1 = scored[0]
    if top1["score"] < conf_threshold:
        return "need_confirm", scored[:5]
    if len(scored) >= 2 and (top1["score"] - scored[1]["score"]) < margin_threshold:
        # 例外：若第一名是 100 分显式 tmdb_id 命中则直接通过
        if top1["score"] < 99.5:
            return "need_confirm", scored[:5]
    return "auto", scored[:5]


def compact_candidates(scored: list[dict]) -> list[dict]:
    """裁剪写入 DB 的轻量候选列表（剥离庞大的 raw detail 避免表膨胀）。"""
    out = []
    for c in (scored or [])[:5]:
        out.append({
            "tmdb_id": c.get("tmdb_id"),
            "kind": c.get("kind"),
            "title": c.get("title"),
            "original_title": c.get("original_title"),
            "year": c.get("year"),
            "poster_path": c.get("poster_path"),
            "backdrop_path": c.get("backdrop_path"),
            "overview": c.get("overview"),
            "rating": c.get("rating"),
            "score": c.get("score"),
            "reasons": c.get("reasons") or [],
        })
    return out


def compute_local_meta_dir(show_rel_dir: str, custom_root: str = "") -> Path:
    """安全解析本地 `.nfo`/海报持久化存储目录（防止路径穿越）。"""
    base = Path(custom_root.strip()).expanduser() if (custom_root or "").strip() else config.MEDIA_META_DIR
    base = base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    safe_segs = [sanitize_filename(seg) for seg in (show_rel_dir or "").replace("\\", "/").split("/") if seg.strip() and seg not in (".", "..")]
    target = base.joinpath(*safe_segs) if safe_segs else base / "unsorted"
    resolved = target.resolve()
    if not resolved.is_relative_to(base):
        resolved = base / sanitize_filename(show_rel_dir or "unsorted")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _pretty_xml(root: ET.Element) -> str:
    raw = ET.tostring(root, encoding="utf-8")
    parsed = minidom.parseString(raw)
    return parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


_XML_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _add_el(parent: ET.Element, tag: str, text: str | int | float | None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        cleaned = _XML_CTRL_RE.sub("", str(text))
        if cleaned != "":
            el.text = cleaned
    return el


def build_movie_nfo(detail: dict) -> str:
    root = ET.Element("movie")
    _add_el(root, "title", detail.get("title") or detail.get("name"))
    _add_el(root, "originaltitle", detail.get("original_title") or detail.get("original_name"))
    _add_el(root, "plot", detail.get("overview"))
    _add_el(root, "outline", (detail.get("overview") or "")[:160])
    rel = str(detail.get("release_date") or "")
    _add_el(root, "premiered", rel)
    _add_el(root, "year", rel[:4] if len(rel) >= 4 else "")
    _add_el(root, "rating", round(float(detail.get("vote_average") or 0), 1))
    _add_el(root, "votes", detail.get("vote_count") or 0)
    _add_el(root, "tmdbid", detail.get("id"))
    ext_ids = detail.get("external_ids") or {}
    if ext_ids.get("imdb_id") or detail.get("imdb_id"):
        _add_el(root, "imdbid", ext_ids.get("imdb_id") or detail.get("imdb_id"))
    uid = _add_el(root, "uniqueid", detail.get("id"))
    uid.set("type", "tmdb")
    uid.set("default", "true")
    for g in (detail.get("genres") or []):
        if g.get("name"):
            _add_el(root, "genre", g["name"])
    for c in ((detail.get("credits") or {}).get("cast") or [])[:10]:
        if c.get("name"):
            ac = ET.SubElement(root, "actor")
            _add_el(ac, "name", c["name"])
            _add_el(ac, "role", c.get("character") or "")
    return _pretty_xml(root)


def build_tvshow_nfo(detail: dict) -> str:
    root = ET.Element("tvshow")
    _add_el(root, "title", detail.get("name") or detail.get("title"))
    _add_el(root, "originaltitle", detail.get("original_name") or detail.get("original_title"))
    _add_el(root, "plot", detail.get("overview"))
    rel = str(detail.get("first_air_date") or "")
    _add_el(root, "premiered", rel)
    _add_el(root, "year", rel[:4] if len(rel) >= 4 else "")
    _add_el(root, "status", detail.get("status") or "")
    _add_el(root, "rating", round(float(detail.get("vote_average") or 0), 1))
    _add_el(root, "tmdbid", detail.get("id"))
    uid = _add_el(root, "uniqueid", detail.get("id"))
    uid.set("type", "tmdb")
    uid.set("default", "true")
    for g in (detail.get("genres") or []):
        if g.get("name"):
            _add_el(root, "genre", g["name"])
    for c in ((detail.get("credits") or {}).get("cast") or [])[:10]:
        if c.get("name"):
            ac = ET.SubElement(root, "actor")
            _add_el(ac, "name", c["name"])
            _add_el(ac, "role", c.get("character") or "")
    return _pretty_xml(root)


def build_season_nfo(tv_detail: dict, season_num: int) -> str:
    root = ET.Element("season")
    s_info = next(
        (s for s in (tv_detail.get("seasons") or []) if int(s.get("season_number") or 0) == season_num),
        {},
    )
    _add_el(root, "seasonnumber", season_num)
    _add_el(root, "title", s_info.get("name") or f"第 {season_num} 季")
    _add_el(root, "plot", s_info.get("overview") or "")
    ad = str(s_info.get("air_date") or "")
    _add_el(root, "premiered", ad)
    _add_el(root, "year", ad[:4] if len(ad) >= 4 else "")
    return _pretty_xml(root)


def build_episode_nfo(ep_info: dict, season_num: int, ep_num: int) -> str:
    root = ET.Element("episodedetails")
    _add_el(root, "title", ep_info.get("name") or f"第 {ep_num} 集")
    _add_el(root, "season", season_num)
    _add_el(root, "episode", ep_num)
    _add_el(root, "plot", ep_info.get("overview") or "")
    _add_el(root, "aired", ep_info.get("air_date") or "")
    _add_el(root, "rating", round(float(ep_info.get("vote_average") or 0), 1))
    if ep_info.get("id"):
        uid = _add_el(root, "uniqueid", ep_info["id"])
        uid.set("type", "tmdb")
        uid.set("default", "true")
    return _pretty_xml(root)


def save_local_metadata(
    meta_root_dir: Path,
    detail: dict,
    kind: str,
    season_nums: list[int] | None = None,
    episode_items: list[tuple[int, int, str]] | None = None,
    api_key: str = "",
    write_nfo: bool = True,
    download_poster: bool = True,
) -> dict:
    """将权威 `.nfo` 与海报/背景图仅写入本地持久化目录（零 115 上传请求）。"""
    saved_files: list[str] = []
    meta_root_dir.mkdir(parents=True, exist_ok=True)

    if write_nfo:
        if kind == "movie":
            nfo_path = meta_root_dir / "movie.nfo"
            nfo_path.write_text(build_movie_nfo(detail), encoding="utf-8")
            saved_files.append(str(nfo_path))
        else:
            tv_nfo = meta_root_dir / "tvshow.nfo"
            tv_nfo.write_text(build_tvshow_nfo(detail), encoding="utf-8")
            saved_files.append(str(tv_nfo))

            tv_id = int(detail.get("id") or 0)
            season_ep_map: dict[int, dict[int, dict]] = {}
            for sn in sorted(set(season_nums or [1])):
                s_dir = meta_root_dir / f"Season {sn:02d}"
                s_dir.mkdir(parents=True, exist_ok=True)
                s_nfo = s_dir / "season.nfo"
                s_nfo.write_text(build_season_nfo(detail, sn), encoding="utf-8")
                saved_files.append(str(s_nfo))
                if tv_id and api_key:
                    eps = get_tv_season_episodes(tv_id, sn, api_key)
                    season_ep_map[sn] = {int(e.get("episode_number") or 0): e for e in eps if e.get("episode_number")}

            for sn, epn, ep_stem in (episode_items or []):
                s_dir = meta_root_dir / f"Season {sn:02d}"
                s_dir.mkdir(parents=True, exist_ok=True)
                ep_info = season_ep_map.get(sn, {}).get(epn, {"episode_number": epn})
                safe_ep_stem = sanitize_filename(ep_stem) or f"S{sn:02d}E{epn:02d}"
                ep_nfo_path = s_dir / f"{safe_ep_stem}.nfo"
                ep_nfo_path.write_text(build_episode_nfo(ep_info, sn, epn), encoding="utf-8")
                saved_files.append(str(ep_nfo_path))

    poster_rel = ""
    if download_poster:
        pp = str(detail.get("poster_path") or "").strip()
        bp = str(detail.get("backdrop_path") or "").strip()
        if pp:
            p_file = meta_root_dir / "poster.jpg"
            if not p_file.exists() or p_file.stat().st_size == 0:
                img_bytes = tmdb_download_image(pp, size="w500")
                if img_bytes:
                    p_file.write_bytes(img_bytes)
            if p_file.exists() and p_file.stat().st_size > 0:
                saved_files.append(str(p_file))
                poster_rel = str(p_file)
        if bp:
            b_file = meta_root_dir / "fanart.jpg"
            if not b_file.exists() or b_file.stat().st_size == 0:
                bg_bytes = tmdb_download_image(bp, size="w780")
                if bg_bytes:
                    b_file.write_bytes(bg_bytes)
            if b_file.exists() and b_file.stat().st_size > 0:
                saved_files.append(str(b_file))

    return {"meta_dir": str(meta_root_dir), "files": saved_files, "poster_local": poster_rel}
