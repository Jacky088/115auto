"""订阅追剧 CRUD 路由（TMDB 选剧路由已拆入 tmdb_browse.py）。"""
import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException

from ... import database as db
from ...core import logstream, subscribe as submod
from ..deps import require_user
from .tmdb_browse import (  # noqa: F401  # 向后兼容重导出
    _DISCOVER_CACHE,
    _POSTER_CACHE,
    _TV_SEASON_CACHE,
    _tmdb_get,
    _tmdb_item,
    tmdb_discover,
    tmdb_poster,
    tmdb_search,
)

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
        row["exclude_keywords"] = submod.excludes_of(row)
        row["auto_disable_movie"] = bool(
            1 if row.get("auto_disable_movie") is None else row.get("auto_disable_movie")
        )
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
    excludes = _clean_aliases(body.get("exclude_keywords"))
    conflict = submod.find_conflict(kw, aliases)
    if conflict:
        raise HTTPException(400, f"订阅「{kw}」与现有订阅「{conflict['keyword']}」重复")
    tmdb_id, kind = _clean_tmdb_meta(body)
    min_quality = _clean_min_quality(body.get("min_quality"))
    auto_dis = 1 if body.get("auto_disable_movie", True) else 0
    db.execute(
        "INSERT INTO subscriptions(keyword,note,aliases,exclude_keywords,tmdb_id,kind,min_quality,auto_disable_movie,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (kw, (body.get("note") or "")[:200],
         json.dumps(aliases, ensure_ascii=False),
         json.dumps(excludes, ensure_ascii=False),
         tmdb_id, kind, min_quality, auto_dis, time.time()))
    submod.invalidate_cache()
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
    if "exclude_keywords" in body:
        excludes = _clean_aliases(body.get("exclude_keywords"))
        db.execute("UPDATE subscriptions SET exclude_keywords=? WHERE id=?",
                   (json.dumps(excludes, ensure_ascii=False), sub_id))
    if "note" in body:
        db.execute("UPDATE subscriptions SET note=? WHERE id=?",
                   (str(body.get("note") or "")[:200], sub_id))
    if "auto_disable_movie" in body:
        db.execute("UPDATE subscriptions SET auto_disable_movie=? WHERE id=?",
                   (1 if body.get("auto_disable_movie") else 0, sub_id))
    # 关键词变更后旧剧的进度不再适用；reset_progress 显式重置也可
    if body.get("reset_progress") or kw_changed:
        _reset_progress_sql(sub_id)
    submod.invalidate_cache()
    return {"ok": True}


@router.delete("/subscriptions/{sub_id}")
def delete_subscription(sub_id: int, _=Depends(require_user)):
    row = db.query("SELECT keyword FROM subscriptions WHERE id=?", (sub_id,), one=True)
    db.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
    submod.invalidate_cache()
    if row:
        logstream.info("subscribe", f"删除追剧订阅: {row['keyword']}")
    return {"ok": True}

