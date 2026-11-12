"""目录整理与媒体刮削核心执行引擎（严格单队列串行、四方互斥门禁、Web↔TG 双向实时联动）。

设计原则：
1. 四方互斥：同一时刻仅允许「定时归档 auto_move / 目录整理 organize / 媒体刮削 scrape」三者之一持有写门禁，
   运行前强制通过 `transfer_gate.prepare_and_drain_transfers(mode=...)` 排空已入队转存任务，结束后于 `finally` 恢复主程序；
2. 零并发防风控：全部 115 目录扫描、重命名、建目录与移动严格经 `client.run_pan_thread` + `pan_call` 单线程串行，
   操作间隔受 `media_op_delay_min~max` 与自适应 `pace_factor()` 控制；
3. 零上传写本地：`.nfo` 与海报仅写入本地 `MEDIA_META_DIR`（`save_local_metadata`），零 115 文件上传请求；
4. 0 秒跳过 + 事后排队推卡 + 选而不交：低置信度条目先挂起 `need_confirm`，刮削完释放主程序后经 `_tg_notify_queue`
   按 1.2s/条 排队发卡片；点选候选只置为 `staged`（已选待提交），点「提交队列」才置为 `queued` 并触发执行；
5. Web ↔ TG 真联动：每次暂存/撤销/忽略/提交均经 `db.transaction()` 原子校验写入，阻止网页已提交/已选后 TG 误覆盖，
   并反向排队通过 `editMessageText` 原位刷新所有受影响 TG 卡片与底部控制台。
"""
from __future__ import annotations

import asyncio
import json
import os
import posixpath
import random
import re
import time
import uuid

from .. import database as db
from ..core import logstream, transfer_gate
from ..core.media_parser import (
    ParsedMedia,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    format_media_target,
    match_subtitle_for_video,
    parse_media_context,
    parse_media_filename,
    resolution_rank,
    sanitize_filename,
)
from ..core.tmdb_scraper import (
    compact_candidates,
    compute_local_meta_dir,
    decide_match,
    get_tmdb_detail,
    recall_and_score,
    remap_absolute_episode,
    save_local_metadata,
)
from ..pan115 import client
from ..pan115.client import normalize_title, strip_duplicate_suffix
from . import jobs

_TASK_LOCKS: dict[str, asyncio.Lock] = {}
_ACTIVE_TASK_REF: dict[str, asyncio.Task | None] = {"organize": None, "scrape": None}
_COMMIT_WAKEUP_REQUESTED: bool = False

STATUS_ZH = {
    "queued": "排队执行中",
    "running": "正在处理",
    "need_confirm": "待确认",
    "staged": "已选待提交",
    "done": "已完成",
    "skipped": "已跳过",
    "failed": "执行失败",
    "ignored": "已忽略",
}


class MediaCancelled(Exception):
    """收到顶栏停止指令，安全中止后续队列条目。"""


class _RiskCircuitBreak(Exception):
    """连续多次触发 115 风控征兆时安全熔断本批写入。"""


def _is_risk_error(err: Exception | str) -> bool:
    """判断异常文本是否命中 115 风控限制关键词。"""
    s = str(err or "").lower()
    if not s:
        return False
    kws = getattr(client, "_RISK_KEYWORDS", ("频繁", "风控", "验证码", "IP登录异常", "too many", "limit", "405"))
    return any(str(k).lower() in s for k in kws)


def _get_lock(name: str) -> asyncio.Lock:
    lk = _TASK_LOCKS.get(name)
    if lk is None:
        lk = asyncio.Lock()
        _TASK_LOCKS[name] = lk
    return lk


def is_system_running() -> bool:
    return str(db.get_setting("running", False)).lower() == "true"


def get_media_settings() -> dict:
    """读取整理刮削相关全部设置项。"""
    keys = (
        "tmdb_api_key",
        "media_append_tmdb_tag",
        "media_organize_template_movie",
        "media_organize_template_tv",
        "media_conflict_policy",
        "media_move_subtitles",
        "media_skip_extras",
        "media_extra_min_mb",
        "media_clean_empty_dirs",
        "media_scrape_confidence_threshold",
        "media_scrape_margin_threshold",
        "media_scrape_write_local_nfo",
        "media_scrape_download_poster",
        "media_scrape_wait_seconds",
        "media_local_meta_dir",
        "media_op_delay_min",
        "media_op_delay_max",
        "media_organize_schedule_enabled",
        "media_organize_schedule_hours",
    )
    out = {}
    for k in keys:
        out[k] = db.get_setting(k, db.DEFAULT_SETTINGS.get(k))
    return out


def _check_not_cancelled():
    if transfer_gate.is_cancel_requested() or not is_system_running():
        raise MediaCancelled("系统已停止运行，整理/刮削队列安全中断")


async def _pacing_sleep(settings: dict) -> None:
    """防风控随机退避延时，并按步长感知取消信号。"""
    _check_not_cancelled()
    try:
        dmin = max(0.2, float(settings.get("media_op_delay_min", 0.8)))
        dmax = max(dmin, float(settings.get("media_op_delay_max", 2.0)))
    except (TypeError, ValueError):
        dmin, dmax = 0.8, 2.0
    wait = random.uniform(dmin, dmax) * max(1.0, client.pace_factor())
    end = time.monotonic() + wait
    while time.monotonic() < end:
        _check_not_cancelled()
        await asyncio.sleep(min(0.4, max(0.05, end - time.monotonic())))


async def _interruptible_batch_rest(
    settings: dict,
    op_counter: int,
    log_mod: str,
    every: int = 15,
    rest_sec: float = 12.0,
) -> None:
    """每连续写入 N 次进行一次可中断长休眠，降低批量操作触发 115 风控的概率。"""
    if op_counter <= 0 or op_counter % every != 0:
        return
    try:
        cfg_max = float(settings.get("media_op_delay_max", 2.0) or 0)
    except (TypeError, ValueError):
        cfg_max = 2.0
    # 测试环境配置极小延迟时跳过长休眠，保持单测快速执行
    if cfg_max < 0.2:
        return
    logstream.info(log_mod, f"☕ 已连续完成 {op_counter} 次 115 写操作，礼貌休眠 {rest_sec:.0f}s 避风控...")
    end = time.monotonic() + rest_sec
    while time.monotonic() < end:
        _check_not_cancelled()
        await asyncio.sleep(min(0.4, max(0.05, end - time.monotonic())))


def _load_db_transfer_hints() -> tuple[dict[str, dict], dict[str, dict]]:
    """加载已转存记录与追更订阅作为第三层解析线索。

    返回 `(by_norm_title, by_sub_id)`，供 `parse_media_context` 快速匹配。
    """
    by_title: dict[str, dict] = {}
    try:
        subs = db.query(
            "SELECT keyword, aliases, last_title, kind, tmdb_id "
            "FROM subscriptions WHERE enabled=1 ORDER BY id DESC LIMIT 300"
        )
        for r in subs:
            names: list[str] = []
            kw = str(r["keyword"] or "").strip()
            if kw:
                names.append(kw)
            lt = str(r["last_title"] or "").strip()
            if lt:
                names.append(lt)
            raw_aliases = str(r["aliases"] or "").strip()
            if raw_aliases:
                try:
                    al = json.loads(raw_aliases)
                    if isinstance(al, list):
                        names.extend(str(a).strip() for a in al if str(a or "").strip())
                except Exception:
                    pass
            disp = kw or lt
            for nm in names:
                nt = normalize_title(nm, fuzzy_t2s=True)
                if nt and nt not in by_title:
                    by_title[nt] = {
                        "title": disp,
                        "kind": str(r["kind"] or ""),
                        "tmdb_id": r["tmdb_id"],
                    }
    except Exception:
        pass
    try:
        tr_rows = db.query(
            "SELECT title, category, target_dir FROM transfers "
            "WHERE status='success' AND title != '' ORDER BY id DESC LIMIT 500"
        )
        for r in tr_rows:
            raw_t = str(r["title"] or "").strip()
            cat = str(r["category"] or "")
            pm = parse_media_filename(raw_t, is_dir=True)
            key = normalize_title(pm.title or raw_t, fuzzy_t2s=True)
            if not key or key in by_title:
                continue
            kind = "movie" if "电影" in cat else ("tv" if any(k in cat for k in ("剧", "动漫", "综艺", "番")) else "")
            by_title[key] = {
                "title": pm.title or raw_t,
                "kind": kind,
                "tmdb_id": pm.tmdb_id,
            }
    except Exception:
        pass
    return by_title, {}


def _match_hint_for_parsed(pm: ParsedMedia, parent_dirs: list[str], hints_by_title: dict[str, dict]) -> dict | None:
    if not hints_by_title:
        return None
    for cand in [pm.title, *(list(reversed(parent_dirs or []))[:2])]:
        nc = normalize_title(cand or "", fuzzy_t2s=True)
        if nc and nc in hints_by_title:
            return hints_by_title[nc]
    return None


def _split_rel_and_name(full_target_dir: str, rel_dir: str, fname: str) -> tuple[str, str]:
    base = "/" + (full_target_dir or "").strip("/")
    sub = (rel_dir or "").strip("/")
    dest_dir = f"{base}/{sub}" if sub else base
    return dest_dir, fname


def _should_skip_extra(file_name: str, size_bytes: int, pm: ParsedMedia, settings: dict) -> bool:
    if not settings.get("media_skip_extras", True):
        return False
    if pm.is_extra:
        return True
    try:
        min_mb = max(0, int(settings.get("media_extra_min_mb", 50)))
    except (TypeError, ValueError):
        min_mb = 50
    if (
        pm.media_type == "movie"
        and not pm.is_bdmv
        and pm.ext != ".iso"
        and min_mb > 0
        and size_bytes > 0
        and size_bytes < min_mb * 1024 * 1024
    ):
        return True
    return False


def _scan_rules_sync(
    rule_ids: list[int] | None,
    mode_col: str,
    settings: dict,
    include_tmdb_hint_only: bool = False,
) -> tuple[list[dict], list[dict]]:
    """同步扫描启用中的 `media_rules` 在 115 上的媒体条目，在 `run_pan_thread` 中调用。

    返回 `(rules, items)`；每个 item 已完成三层 `parse_media_context` 与配套字幕配对。
    """
    sql = f"SELECT * FROM media_rules WHERE enabled=1 AND {mode_col}=1"
    params: list = []
    if rule_ids:
        placeholders = ",".join("?" for _ in rule_ids)
        sql += f" AND id IN ({placeholders})"
        params.extend(int(x) for x in rule_ids)
    sql += " ORDER BY id ASC"
    rules = [dict(r) for r in db.query(sql, tuple(params))]
    if not rules:
        return [], []

    hints_by_title, _ = _load_db_transfer_hints()
    items: list[dict] = []

    for rule in rules:
        src_dir = "/" + str(rule["source_dir"] or "").strip("/")
        dest_root = "/" + str(rule.get("target_dir") or src_dir).strip("/")
        try:
            src_cid = client.resolve_dir_cid(src_dir)
            if not src_cid:
                logstream.warn("organize" if mode_col == "organize_enabled" else "scrape",
                               f"规则「{rule.get('name') or src_dir}」源目录在 115 上不存在，跳过")
                continue
            tree = client.walk_media_tree(int(src_cid), src_dir, max_depth=4)
        except Exception as e:
            logstream.warn("organize" if mode_col == "organize_enabled" else "scrape",
                           f"扫描规则目录 {src_dir} 失败: {e}")
            continue

        # 统计同父目录下集数情况，用于 TMDB 物理对账与季度识别
        dir_stats: dict[str, dict] = {}
        raw_entries: list[dict] = []

        # 1) BDMV / VIDEO_TS 原盘目录
        for b in tree.get("bdmv_dirs") or []:
            pm = parse_media_context(b["name"], parent_dirs=b.get("parent_dirs") or [], is_dir=True)
            pm.is_bdmv = True
            pm.media_type = rule.get("media_type") if rule.get("media_type") in ("movie", "tv") else pm.media_type
            hint = _match_hint_for_parsed(pm, b.get("parent_dirs") or [], hints_by_title)
            if hint:
                pm = parse_media_context(b["name"], parent_dirs=b.get("parent_dirs") or [], db_hint=hint, is_dir=True)
                pm.is_bdmv = True
                if rule.get("media_type") in ("movie", "tv"):
                    pm.media_type = rule["media_type"]
            raw_entries.append({
                "rule_id": int(rule["id"]),
                "rule_name": rule.get("name") or src_dir,
                "rule_media_type": rule.get("media_type") or "auto",
                "source_dir": src_dir,
                "dest_root": dest_root,
                "source_cid": str(src_cid),
                "file_id": str(b["id"]),
                "parent_cid": str(b.get("parent_cid") or ""),
                "orig_name": b["name"],
                "orig_path": b["path"],
                "dir_path": posixpath.dirname(b["path"]),
                "parent_dirs": b.get("parent_dirs") or [],
                "size": 0,
                "sha1": "",
                "is_bdmv": True,
                "parsed": pm,
                "subtitles": [],
                "descendant_dir_cids": [str(b.get("parent_cid") or "")] if b.get("parent_cid") else [],
            })

        # 2) 普通视频文件 + 同目录外挂字幕配对
        for f in tree.get("files") or []:
            fname = f["name"]
            _, ext = os.path.splitext(strip_duplicate_suffix(fname)[0])
            if ext.lower() not in VIDEO_EXTENSIONS:
                continue
            pdirs = f.get("parent_dirs") or []
            pm = parse_media_context(fname, parent_dirs=pdirs, is_dir=False)
            if rule.get("media_type") in ("movie", "tv") and pm.episode is None and pm.season is None:
                pm.media_type = rule["media_type"]
            hint = _match_hint_for_parsed(pm, pdirs, hints_by_title)
            if hint:
                pm = parse_media_context(fname, parent_dirs=pdirs, db_hint=hint, is_dir=False)
                if rule.get("media_type") in ("movie", "tv") and pm.episode is None and pm.season is None:
                    pm.media_type = rule["media_type"]

            if _should_skip_extra(fname, int(f.get("size") or 0), pm, settings):
                continue

            siblings = f.get("sibling_entries") or []
            sub_names = [s["name"] for s in siblings if os.path.splitext(strip_duplicate_suffix(s["name"])[0])[1].lower() in SUBTITLE_EXTENSIONS]
            sub_by_name = {s["name"]: s for s in siblings}
            matched_subs = []
            if settings.get("media_move_subtitles", True) and sub_names:
                for sub_fn, lang_tag in match_subtitle_for_video(fname, sub_names):
                    s_entry = sub_by_name.get(sub_fn)
                    if s_entry:
                        matched_subs.append({
                            "id": str(s_entry["id"]),
                            "name": sub_fn,
                            "size": int(s_entry.get("size") or 0),
                            "sha1": s_entry.get("sha1") or "",
                            "lang_tag": lang_tag,
                            "ext": os.path.splitext(strip_duplicate_suffix(sub_fn)[0])[1].lower(),
                        })

            dp = f.get("dir_path") or src_dir
            st = dir_stats.setdefault(dp, {"video_count": 0, "max_ep": 0})
            st["video_count"] += 1
            if pm.episode and pm.episode > st["max_ep"]:
                st["max_ep"] = pm.episode

            raw_entries.append({
                "rule_id": int(rule["id"]),
                "rule_name": rule.get("name") or src_dir,
                "rule_media_type": rule.get("media_type") or "auto",
                "source_dir": src_dir,
                "dest_root": dest_root,
                "source_cid": str(src_cid),
                "file_id": str(f["id"]),
                "parent_cid": str(f.get("parent_cid") or ""),
                "orig_name": fname,
                "orig_path": f"{dp.rstrip('/')}/{fname}",
                "dir_path": dp,
                "parent_dirs": pdirs,
                "size": int(f.get("size") or 0),
                "sha1": f.get("sha1") or "",
                "is_bdmv": False,
                "parsed": pm,
                "subtitles": matched_subs,
            })

        # 补充 ep_stats（如果 parent_dir 是 Season XX，则尽量累计上一级剧集内全部分散视频，准确识别多季剧）
        for it in raw_entries:
            it["ep_stats"] = dict(dir_stats.get(it["dir_path"], {"video_count": 1, "max_ep": it["parsed"].episode or 0}))
            items.append(it)

    return rules, items


# ---------- 公共 115 文件移动重命名与冲突处理（整理与刮削复用） ----------

def _pick_unique_name(base_name: str, existing_names: set[str], is_dir: bool) -> str:
    if base_name not in existing_names:
        return base_name
    stem, ext = ("", "") if is_dir else os.path.splitext(base_name)
    if is_dir:
        stem = base_name
    for idx in range(1, 50):
        cand = f"{stem} ({idx}){ext}"
        if cand not in existing_names:
            return cand
    return f"{stem}_{int(time.time()) % 10000}{ext}"


def _move_and_rename_single_sync(
    file_id: str,
    orig_name: str,
    parent_cid: str,
    dest_cid: int,
    target_name: str,
    src_sibling_names: set[str],
    dest_existing_names: set[str],
) -> str:
    """安全执行单条目移动与重命名（防止同目录/目标目录重名碰车）。返回动作类型。"""
    same_parent = str(parent_cid) == str(dest_cid)
    need_rename = (orig_name != target_name)

    if same_parent:
        if not need_rename:
            return "noop"
        if target_name in dest_existing_names:
            raise client.Pan115Error(f"同目录存在冲突文件 {target_name}")
        client.rename_entry(file_id, target_name)
        dest_existing_names.discard(orig_name)
        dest_existing_names.add(target_name)
        return "rename"

    # 跨目录移动：若 target_name 在原目录不冲突且需改名，先改名再移动；否则先移过去再改名
    if need_rename and target_name not in src_sibling_names:
        client.rename_entry(file_id, target_name)
        src_sibling_names.discard(orig_name)
        src_sibling_names.add(target_name)
        client.move_entries([file_id], int(dest_cid))
        dest_existing_names.add(target_name)
        return "move_rename"

    client.move_entries([file_id], int(dest_cid))
    if need_rename:
        client.rename_entry(file_id, target_name)
        dest_existing_names.add(target_name)
        return "move_rename"
    dest_existing_names.add(orig_name)
    return "move"


def _apply_media_placement_sync(
    item: dict,
    rel_dir: str,
    target_name: str,
    settings: dict,
    batch_id: str,
    task_id: int = 0,
    tmdb_id: int | None = None,
    dest_root_cache: dict[str, int] | None = None,
    subdir_cache: dict[tuple[int, str], int] | None = None,
    dir_entries_cache: dict[int, list[dict]] | None = None,
    moved_parent_cids: list[str] | None = None,
) -> dict:
    """在 115 网盘落盘（确保目标子目录、解决重名冲突、移动/重命名主视频与配套字幕、写 `organized_records`）。"""
    dest_root = "/" + str(item.get("dest_root") or item["source_dir"]).strip("/")
    full_dest_dir, final_name = _split_rel_and_name(dest_root, rel_dir, target_name)

    # 若原位置与目标位置及文件名已完全一致，则幂等跳过
    if (item.get("orig_path") or "").rstrip("/") == f"{full_dest_dir.rstrip('/')}/{final_name}":
        return {"status": "skipped", "in_place": True, "reason": "已在目标规范位置", "dest_path": full_dest_dir, "dest_name": final_name}

    dcache = dest_root_cache if dest_root_cache is not None else {}
    scache = subdir_cache if subdir_cache is not None else {}
    ecache = dir_entries_cache if dir_entries_cache is not None else {}

    if dest_root not in dcache:
        dcache[dest_root] = int(client.mkdirs(dest_root))
    dest_root_cid = dcache[dest_root]

    sub_key = (dest_root_cid, rel_dir.strip("/"))
    if sub_key not in scache:
        scache[sub_key] = int(client.ensure_subdir(dest_root_cid, rel_dir))
    dest_cid = scache[sub_key]

    if dest_cid not in ecache:
        ecache[dest_cid] = client.list_entries(dest_cid, limit=2000)
    dest_entries = ecache[dest_cid]
    dest_by_name = {e["name"]: e for e in dest_entries}
    dest_names = set(dest_by_name.keys())

    # 源父目录同级文件名（用于决定先 rename 还是先 move）
    parent_cid_int = int(str(item.get("parent_cid") or "0") or 0)
    if parent_cid_int and parent_cid_int not in ecache:
        try:
            ecache[parent_cid_int] = client.list_entries(parent_cid_int, limit=2000)
        except Exception:
            ecache[parent_cid_int] = []
    src_names = {e["name"] for e in ecache.get(parent_cid_int, [])}

    is_bdmv = bool(item.get("is_bdmv"))
    conflict_policy = str(settings.get("media_conflict_policy") or "skip")
    action_note = ""

    if final_name in dest_by_name:
        existing = dest_by_name[final_name]
        if str(existing["id"]) == str(item["file_id"]):
            return {"status": "skipped", "in_place": True, "reason": "目标文件已是当前项", "dest_path": full_dest_dir, "dest_name": final_name}

        if conflict_policy == "keep_both":
            final_name = _pick_unique_name(final_name, dest_names, is_dir=is_bdmv)
        elif conflict_policy == "replace_if_better":
            old_pm = parse_media_filename(existing["name"], is_dir=is_bdmv)
            new_pm: ParsedMedia = item["parsed"]
            old_rank = resolution_rank(old_pm.resolution)
            new_rank = resolution_rank(new_pm.resolution)
            old_sz = int(existing.get("size") or 0)
            new_sz = int(item.get("size") or 0)
            better = (new_rank > old_rank) or (new_rank == old_rank and new_sz > int(old_sz * 1.15) and new_sz > 0)
            if not better:
                return {"status": "skipped", "in_place": False, "reason": f"目标已有同名且新版本画质未更优({new_pm.resolution or '未知'})",
                        "dest_path": full_dest_dir, "dest_name": final_name}
            trash_cid = int(client.ensure_subdir(dest_cid, "_待清理旧版"))
            client.move_entries([str(existing["id"])], trash_cid)
            dest_names.discard(existing["name"])
            dest_by_name.pop(existing["name"], None)
            action_note = "replace_better"
        else:
            return {"status": "skipped", "in_place": False, "reason": "目标已存在同名文件（按策略跳过）",
                    "dest_path": full_dest_dir, "dest_name": final_name}

    action = _move_and_rename_single_sync(
        file_id=str(item["file_id"]),
        orig_name=item["orig_name"],
        parent_cid=str(item["parent_cid"]),
        dest_cid=dest_cid,
        target_name=final_name,
        src_sibling_names=src_names,
        dest_existing_names=dest_names,
    )
    if action_note:
        action = action_note

    # 缓存失效目标目录条目
    ecache.pop(dest_cid, None)
    if parent_cid_int:
        ecache.pop(parent_cid_int, None)
        if moved_parent_cids is not None and str(parent_cid_int) != str(item.get("source_cid")):
            moved_parent_cids.append(str(parent_cid_int))

    now = time.time()
    db.insert(
        "INSERT INTO organized_records(task_id,file_id,parent_cid,sha1,size,orig_path,orig_name,"
        "dest_cid,dest_path,dest_name,action,tmdb_id,batch_id,undone,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
        (
            int(task_id or 0),
            str(item["file_id"]),
            str(item.get("parent_cid") or ""),
            str(item.get("sha1") or ""),
            int(item.get("size") or 0),
            str(item.get("orig_path") or ""),
            str(item["orig_name"]),
            str(dest_cid),
            full_dest_dir,
            final_name,
            action,
            int(tmdb_id) if tmdb_id else None,
            batch_id,
            now,
        ),
    )

    # 同步迁移配套外挂字幕
    sub_moved = 0
    if not is_bdmv and settings.get("media_move_subtitles", True):
        target_stem = os.path.splitext(final_name)[0]
        for sub in (item.get("subtitles") or []):
            sub_target = sanitize_filename(f"{target_stem}{sub.get('lang_tag', '')}{sub.get('ext', '')}")
            if not sub_target:
                continue
            try:
                cur_dest_entries = client.list_entries(dest_cid, limit=2000)
                cur_dest_names = {e["name"] for e in cur_dest_entries}
                if sub_target in cur_dest_names:
                    continue
                sub_action = _move_and_rename_single_sync(
                    file_id=str(sub["id"]),
                    orig_name=sub["name"],
                    parent_cid=str(item["parent_cid"]),
                    dest_cid=dest_cid,
                    target_name=sub_target,
                    src_sibling_names=src_names,
                    dest_existing_names=cur_dest_names,
                )
                sub_moved += 1
                db.insert(
                    "INSERT INTO organized_records(task_id,file_id,parent_cid,sha1,size,orig_path,orig_name,"
                    "dest_cid,dest_path,dest_name,action,tmdb_id,batch_id,undone,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                    (
                        int(task_id or 0),
                        str(sub["id"]),
                        str(item.get("parent_cid") or ""),
                        str(sub.get("sha1") or ""),
                        int(sub.get("size") or 0),
                        f"{posixpath.dirname(item['orig_path'])}/{sub['name']}",
                        sub["name"],
                        str(dest_cid),
                        full_dest_dir,
                        sub_target,
                        f"sub_{sub_action}",
                        int(tmdb_id) if tmdb_id else None,
                        batch_id,
                        time.time(),
                    ),
                )
            except Exception as se:
                logstream.warn("organize", f"配套字幕 {sub['name']} 随动失败（主视频已就位）: {se}")

    return {"status": "done", "action": action, "dest_path": full_dest_dir, "dest_name": final_name, "subtitles_moved": sub_moved}


def _clean_empty_dirs_sync(candidate_cids: list[str], protect_cids: set[str]) -> int:
    """在批末安全清理已搬空的子目录（绝不删除规则根目录自身）。"""
    removed = 0
    seen: set[str] = set()
    for cid in candidate_cids:
        scid = str(cid or "").strip()
        if not scid or scid in seen or scid in protect_cids:
            continue
        seen.add(scid)
        try:
            entries = client.list_entries(int(scid), limit=50)
            if not entries:
                client.delete_entries([scid])
                removed += 1
        except Exception:
            pass
    return removed


# ---------- 模块 A：目录整理（Preview + 队列运行） ----------

async def preview_organize(rule_ids: list[int] | None = None) -> dict:
    """Dry-Run 预览待整理条目（只读 115 目录树与本地规则计算，零网盘写操作）。"""
    if transfer_gate.is_exclusive_busy():
        return {"ok": False, "msg": "当前有独占写任务正在执行，请待结束后再预览", "items": []}
    settings = await asyncio.to_thread(get_media_settings)
    rules, items = await client.run_pan_thread(_scan_rules_sync, rule_ids, "organize_enabled", settings)
    preview_rows = []
    for it in items:
        pm: ParsedMedia = it["parsed"]
        rel_dir, fname = format_media_target(pm, settings)
        dest_dir, final_name = _split_rel_and_name(it["dest_root"], rel_dir, fname)
        target_full = f"{dest_dir.rstrip('/')}/{final_name}"
        already_ok = (it["orig_path"].rstrip("/") == target_full)
        preview_rows.append({
            "rule_id": it["rule_id"],
            "rule_name": it["rule_name"],
            "file_id": it["file_id"],
            "orig_name": it["orig_name"],
            "orig_path": it["orig_path"],
            "target_path": dest_dir,
            "target_name": final_name,
            "media_type": pm.media_type,
            "parsed_title": pm.title,
            "year": pm.year,
            "season": pm.season,
            "episode": pm.episode,
            "tmdb_id": pm.tmdb_id,
            "is_bdmv": it["is_bdmv"],
            "subtitle_count": len(it.get("subtitles") or []),
            "already_ok": already_ok,
        })
    return {
        "ok": True,
        "rule_count": len(rules),
        "total": len(preview_rows),
        "pending_count": sum(1 for r in preview_rows if not r["already_ok"]),
        "items": preview_rows,
    }


def _format_media_detail_lines(done_items: list[dict], max_groups: int = 6) -> list[str]:
    """按「作品/剧集」聚合已整理或已刮削条目，生成适合纯文本 TG 通知的精简明细列表。"""
    if not done_items:
        return []
    groups: dict[tuple, dict] = {}
    for it in done_items:
        mtype = str(it.get("media_type") or "movie")
        title = str(it.get("title") or "").strip() or "未命名媒体"
        year = str(it.get("year") or "").strip()
        tmdb_id = it.get("tmdb_id")
        norm = normalize_title(title, fuzzy_t2s=True) or title.lower()
        key = (mtype, norm, year, str(tmdb_id or ""))
        g = groups.get(key)
        dpath = str(it.get("dest_path") or "").rstrip("/")
        dname = str(it.get("dest_name") or "").strip()
        item_paths = [str(p).rstrip("/") for p in (it.get("dest_paths") or []) if str(p).strip()]
        if dpath and dpath not in item_paths:
            item_paths.insert(0, dpath)
        item_cnt = max(1, int(it.get("count") or 1))
        sub_cnt = int(it.get("sub_count") or 0)
        has_nfo = bool(it.get("has_nfo"))
        has_poster = bool(it.get("has_poster"))
        if g is None:
            groups[key] = {
                "media_type": mtype,
                "title": title,
                "year": year,
                "tmdb_id": tmdb_id,
                "count": item_cnt,
                "sub_count": sub_cnt,
                "is_bdmv": bool(it.get("is_bdmv")),
                "has_nfo": has_nfo,
                "has_poster": has_poster,
                "first_path": dpath,
                "first_name": dname,
                "paths": item_paths,
            }
        else:
            g["count"] += item_cnt
            g["sub_count"] += sub_cnt
            g["has_nfo"] = g["has_nfo"] or has_nfo
            g["has_poster"] = g["has_poster"] or has_poster
            for p in item_paths:
                if p and p not in g["paths"]:
                    g["paths"].append(p)

    group_list = list(groups.values())
    lines: list[str] = []
    for idx, g in enumerate(group_list[:max_groups], 1):
        icon = "📺" if g["media_type"] == "tv" else "🎬"
        yr_part = f" ({g['year']})" if g["year"] else ""
        tmdb_part = f" [tmdbid={g['tmdb_id']}]" if g["tmdb_id"] else ""
        unit = "集" if g["media_type"] == "tv" else ("套" if g["is_bdmv"] else "部")
        sub_part = f"，含 {g['sub_count']} 个字幕" if g["sub_count"] > 0 else ""
        meta_tags = []
        if g["has_nfo"]:
            meta_tags.append("NFO")
        if g["has_poster"]:
            meta_tags.append("海报")
        meta_part = f"｜{' + '.join(meta_tags)}" if meta_tags else ""

        paths = g["paths"]
        if g["media_type"] == "tv" or g["is_bdmv"] or g["count"] > 1:
            if len(paths) > 1:
                try:
                    common = posixpath.commonpath(paths)
                    target_disp = (common.rstrip("/") + "/") if common and common != "/" else (paths[0] + "/")
                except Exception:
                    target_disp = (paths[0] + "/") if paths else "/"
            else:
                target_disp = (paths[0] + "/") if paths else "/"
        else:
            fp = g["first_path"]
            fn = g["first_name"]
            target_disp = f"{fp}/{fn}" if fp and fn else (fp or fn or "/")

        lines.append(f"{idx}. {icon} {g['title']}{yr_part}{tmdb_part}（{g['count']} {unit}{sub_part}{meta_part}）")
        lines.append(f"   ↳ {target_disp}")

    if len(group_list) > max_groups:
        rem_groups = group_list[max_groups:]
        rem_count = sum(g["count"] for g in rem_groups)
        lines.append(f"… 另有 {len(rem_groups)} 部作品（共 {rem_count} 项）已就位，详情见网页任务记录")

    return lines


_format_organize_detail_lines = _format_media_detail_lines


def _build_organize_tg_report(
    *,
    outcome: str,
    trigger: str,
    batch_id: str,
    elapsed_sec: int,
    rule_cnt: int,
    total_candidates: int,
    done_cnt: int,
    skip_cnt: int,
    fail_cnt: int,
    removed_dirs: int,
    done_items: list[dict],
    fail_items: list[tuple[str, str]],
    error_msg: str = "",
    system_running: bool = True,
) -> str | None:
    """构造「目录整理」纯文本 TG 通知；定时任务 0 移动 0 失败时静默返回 None。"""
    if trigger != "manual" and outcome == "done" and done_cnt == 0 and fail_cnt == 0:
        return None

    trig_zh = "手动执行" if trigger == "manual" else "定时计划执行"
    dur_zh = f"（耗时 {max(0, int(elapsed_sec))}s）"
    resume_line = (
        "▶️ 主程序转存与云下载已恢复运行"
        if system_running
        else "ℹ️ 系统当前处于停止状态，未执行部分保留原状"
    )

    if outcome == "risk_aborted":
        head = "🛑 115 目录整理触发风控熔断"
    elif outcome == "cancelled":
        head = "⏸ 115 目录整理已安全中断"
    elif outcome == "error":
        head = "❌ 115 目录整理执行异常"
    elif fail_cnt > 0 and done_cnt > 0:
        head = "⚠️ 115 目录整理部分完成（含异常）"
    elif fail_cnt > 0:
        head = "❌ 115 目录整理执行失败"
    elif done_cnt == 0:
        head = "📁 115 目录整理完成（无需变动）"
    else:
        head = "📁 115 目录整理完成"

    if outcome == "done" and done_cnt == 0 and fail_cnt == 0:
        scope_txt = (
            "暂无已启用的整理规则"
            if rule_cnt == 0
            else f"{rule_cnt} 条规则，共检查 {total_candidates} 项（均已符合命名规范或已跳过）"
        )
        return "\n".join([
            head,
            "",
            f"• 触发方式：{trig_zh}{dur_zh}",
            f"• 扫描范围：{scope_txt}",
            resume_line,
        ])

    lines = [
        head,
        "",
        f"• 触发方式：{trig_zh}{dur_zh}",
        f"• 整理统计：成功 {done_cnt} 项｜跳过 {skip_cnt} 项｜失败 {fail_cnt} 项",
    ]
    if removed_dirs > 0:
        lines.append(f"• 空目录清理：已清理 {removed_dirs} 个搬空目录")
    if error_msg and outcome in ("risk_aborted", "cancelled", "error"):
        lines.append(f"• 中止原因：{str(error_msg)[:180]}")

    detail_lines = _format_media_detail_lines(done_items, max_groups=6)
    if detail_lines:
        lines.append("")
        lines.append("整理明细：")
        lines.extend(detail_lines)

    if fail_items:
        lines.append("")
        lines.append("失败摘要（仅显示前 3 条）：")
        for fname, err_txt in fail_items[:3]:
            lines.append(f"• {fname}：{str(err_txt or '未知错误')[:120]}")
        if len(fail_items) > 3:
            lines.append(f"• … 另有 {len(fail_items) - 3} 项失败，详情见网页任务记录")

    lines.append("")
    if done_cnt > 0:
        undo_note = "（已完成部分仍可一键撤销）" if (outcome != "done" or fail_cnt > 0) else ""
        lines.append(f"📌 批次编号：{batch_id}{undo_note}")
    lines.append(resume_line)
    if done_cnt > 0:
        lines.append("💡 如需回退，可在网页「操作账本」按批次一键撤销")

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850].rstrip() + "\n…（内容较长已截断，完整记录请查看网页任务列表）"
    return text


def _build_scrape_tg_report(
    *,
    mode: str,
    outcome: str,
    elapsed_sec: int,
    rule_cnt: int,
    total_candidates: int,
    auto_cnt: int,
    confirm_cnt: int,
    skip_cnt: int,
    fail_cnt: int,
    removed_dirs: int,
    auto_items: list[dict],
    pending_items: list[dict],
    fail_items: list[tuple[str, str]],
    error_msg: str = "",
    system_running: bool = True,
) -> str | None:
    """构造「媒体刮削」纯文本 TG 通知（无交互按钮；所有待确认项引导前往网页提交队列）。"""
    if mode == "commit_only" and outcome == "done" and auto_cnt == 0 and fail_cnt == 0:
        return None

    dur_zh = f"（耗时 {max(0, int(elapsed_sec))}s）"
    resume_line = (
        "▶️ 主程序转存与云下载已恢复运行"
        if system_running
        else "ℹ️ 系统当前处于停止状态，未执行部分保留原状"
    )
    is_commit = mode == "commit_only"
    subj = "刮削提交队列" if is_commit else "媒体刮削"

    if outcome == "risk_aborted":
        head = f"🛑 115 {subj}触发风控熔断"
    elif outcome == "cancelled":
        head = f"⏸ 115 {subj}已安全中断"
    elif outcome == "error":
        head = f"❌ 115 {subj}执行异常"
    elif fail_cnt > 0 and (auto_cnt > 0 or confirm_cnt > 0):
        head = f"⚠️ 115 {subj}部分完成（含异常）"
    elif fail_cnt > 0:
        head = f"❌ 115 {subj}执行失败"
    elif is_commit:
        head = "🚀 115 刮削提交队列执行完毕"
    elif auto_cnt == 0 and confirm_cnt == 0:
        head = "🎬 115 媒体刮削完成（无新增可刮削条目）"
    else:
        head = "🎬 115 媒体刮削本轮完成"

    if not is_commit and outcome == "done" and auto_cnt == 0 and confirm_cnt == 0 and fail_cnt == 0:
        if rule_cnt == 0:
            scope_txt = "暂无已启用的刮削规则"
        elif skip_cnt > 0:
            scope_txt = f"{rule_cnt} 条规则，共检查 {total_candidates} 项（其中 {skip_cnt} 项已在待确认/排队队列中）"
        else:
            scope_txt = f"{rule_cnt} 条规则，共检查 {total_candidates} 项（均已处理或无新媒体）"
        return "\n".join([
            head,
            "",
            f"• 执行耗时：{max(0, int(elapsed_sec))}s",
            f"• 扫描范围：{scope_txt}",
            resume_line,
        ])

    lines = [head, ""]
    if is_commit:
        lines.append(f"• 执行统计：成功落盘 {auto_cnt} 项｜失败 {fail_cnt} 项{dur_zh}")
    else:
        lines.append(
            f"• 刮削统计：自动落盘 {auto_cnt} 项｜跳过待确认 {confirm_cnt} 部｜已忽略/跳过 {skip_cnt} 项｜失败 {fail_cnt} 项{dur_zh}"
        )
    if removed_dirs > 0:
        lines.append(f"• 空目录清理：已清理 {removed_dirs} 个搬空目录")
    if error_msg and outcome in ("risk_aborted", "cancelled", "error"):
        lines.append(f"• 中止原因：{str(error_msg)[:180]}")

    detail_lines = _format_media_detail_lines(auto_items, max_groups=6)
    if detail_lines:
        lines.append("")
        has_local_meta = any(it.get("has_nfo") or it.get("has_poster") for it in auto_items)
        meta_suffix = "（本地已写 NFO / 海报）" if has_local_meta else ""
        sec_title = f"已确认落盘明细{meta_suffix}：" if is_commit else f"已自动匹配落盘{meta_suffix}："
        lines.append(sec_title)
        lines.extend(detail_lines)

    if not is_commit and pending_items:
        lines.append("")
        if len(pending_items) > 6:
            lines.append(f"以下作品未自动锁定，已跳过并加入网页「待确认列表」（共 {len(pending_items)} 部，前 6 部）：")
        else:
            lines.append("以下作品未自动锁定，已跳过并加入网页「待确认列表」：")
        for idx, p in enumerate(pending_items[:6], 1):
            icon = "📺" if p.get("media_type") == "tv" else "🎬"
            disp = str(p.get("title") or p.get("orig_name") or "未命名").strip()
            yr = str(p.get("year") or "").strip()
            yr_part = f" ({yr})" if yr and yr not in disp else ""
            ep_cnt = int(p.get("ep_count") or 1)
            ep_part = f"（共 {ep_cnt} 集）" if ep_cnt > 1 else ""
            top1 = p.get("top1")
            if top1:
                t1_title = str(top1.get("title") or "?")
                t1_yr = f"({top1['year']})" if top1.get("year") else ""
                t1_sc = float(top1.get("score") or 0)
                hint = f"最高候选 {t1_title}{t1_yr} · {t1_sc:.0f}分"
            else:
                hint = "未匹配到高分候选，需网页手动搜片"
            lines.append(f"{idx}. {icon} {disp}{yr_part}{ep_part}｜{hint}")
        if len(pending_items) > 6:
            lines.append(f"… 另有 {len(pending_items) - 6} 部待确认作品，请打开网页查看")
        lines.append("👉 请前往网页「整理刮削 → 待确认列表」点选候选后点击「提交队列」")

    if fail_items:
        lines.append("")
        lines.append("失败摘要（仅显示前 3 条）：")
        for fname, err_txt in fail_items[:3]:
            lines.append(f"• {fname}：{str(err_txt or '未知错误')[:120]}")
        if len(fail_items) > 3:
            lines.append(f"• … 另有 {len(fail_items) - 3} 项失败，详情见网页任务记录")

    lines.append("")
    lines.append(resume_line)

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850].rstrip() + "\n…（内容较长已截断，完整记录请查看网页任务列表）"
    return text


async def execute_organize_queue(rule_ids: list[int] | None = None, trigger: str = "manual") -> dict:
    """模块 A：目录整理严格单队列顺序执行。"""
    if not is_system_running():
        return {"ok": False, "msg": "系统处于停止状态，请先点击顶栏「运行」启动系统"}

    lock = _get_lock("organize")
    if lock.locked():
        return {"ok": False, "msg": "目录整理正在执行中，请勿重复发起"}

    started_at = time.monotonic()
    settings = await asyncio.to_thread(get_media_settings)

    ok, busy_msg = transfer_gate.try_begin_exclusive("organize")
    if not ok:
        return {"ok": False, "msg": busy_msg}

    batch_id = f"org_{uuid.uuid4().hex[:10]}"
    done_cnt = 0
    skip_cnt = 0
    fail_cnt = 0
    rule_cnt = 0
    total = 0
    removed_dirs = 0
    done_items: list[dict] = []
    fail_items: list[tuple[str, str]] = []
    outcome = "done"
    outcome_msg = ""
    result_payload: dict

    try:
        async with lock:
            transfer_gate.clear_cancel()
            transfer_gate.set_media_progress({"mode": "organize", "phase": "draining", "current": 0, "total": 0, "label": "等待转存队列消化"})
            drained = await transfer_gate.prepare_and_drain_transfers(mode="organize")
            if drained is False:
                raise MediaCancelled("排空阶段收到中断或停止信号，目录整理安全中止")
            _check_not_cancelled()

            transfer_gate.set_media_progress({"mode": "organize", "phase": "scanning", "current": 0, "total": 0, "label": "正在扫描待整理目录"})
            rules, items = await client.run_pan_thread(_scan_rules_sync, rule_ids, "organize_enabled", settings)
            rule_cnt = len(rules)
            total = len(items)
            if not rules:
                result_payload = {
                    "ok": True, "batch_id": batch_id, "total": 0, "done": 0, "skipped": 0, "failed": 0,
                    "msg": "没有启用的整理规则",
                }
            else:
                logstream.info("organize", f"▶ 开始执行目录整理（触发：{trigger}，规则 {len(rules)} 条，候选 {total} 项）")
                dest_root_cache: dict[str, int] = {}
                subdir_cache: dict[tuple[int, str], int] = {}
                dir_entries_cache: dict[int, list[dict]] = {}
                moved_parent_cids: list[str] = []
                protect_cids = {str(it.get("source_cid") or "") for it in items}
                rule_counts: dict[int, int] = {}
                consec_risk = 0
                write_ops = 0

                for idx, it in enumerate(items, 1):
                    _check_not_cancelled()
                    pm: ParsedMedia = it["parsed"]
                    rel_dir, fname = format_media_target(pm, settings)
                    full_dest_dir, final_name = _split_rel_and_name(it["dest_root"], rel_dir, fname)
                    transfer_gate.set_media_progress({
                        "mode": "organize", "phase": "running",
                        "current": idx, "total": total, "label": f"正在整理: {it['orig_name']}",
                    })

                    now = time.time()
                    tid = await asyncio.to_thread(
                        db.insert,
                        "INSERT INTO media_tasks(task_kind,rule_id,media_type,source_dir,source_cid,file_id,orig_name,orig_path,"
                        "parsed_json,target_path,target_name,status,batch_id,created_at,updated_at) VALUES('organize',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (it["rule_id"], pm.media_type, it["source_dir"], it["source_cid"], it["file_id"],
                         it["orig_name"], it["orig_path"], json.dumps(pm.to_dict(), ensure_ascii=False),
                         full_dest_dir, final_name, "running", batch_id, now, now),
                    )

                    try:
                        res = await client.run_pan_thread(
                            _apply_media_placement_sync,
                            it, rel_dir, fname, settings, batch_id, tid, pm.tmdb_id,
                            dest_root_cache, subdir_cache, dir_entries_cache, moved_parent_cids,
                        )
                        st = res["status"]
                        consec_risk = 0
                        if st == "done":
                            done_cnt += 1
                            write_ops += 1
                            rule_counts[it["rule_id"]] = rule_counts.get(it["rule_id"], 0) + 1
                            done_items.append({
                                "title": pm.title or it["orig_name"],
                                "year": str(pm.year or ""),
                                "tmdb_id": pm.tmdb_id,
                                "media_type": pm.media_type,
                                "dest_path": res["dest_path"],
                                "dest_name": res["dest_name"],
                                "sub_count": int(res.get("subtitles_moved") or 0),
                                "is_bdmv": bool(it.get("is_bdmv")),
                            })
                        else:
                            skip_cnt += 1
                        await asyncio.to_thread(
                            db.execute,
                            "UPDATE media_tasks SET status=?, target_path=?, target_name=?, error=?, updated_at=?, finished_at=? WHERE id=?",
                            (st, res["dest_path"], res["dest_name"], res.get("reason", ""), time.time(), time.time(), tid),
                        )
                    except Exception as e:
                        fail_cnt += 1
                        fail_items.append((it["orig_name"], str(e)))
                        logstream.warn("organize", f"整理失败 {it['orig_name']}: {e}")
                        await asyncio.to_thread(
                            db.execute,
                            "UPDATE media_tasks SET status='failed', error=?, updated_at=?, finished_at=? WHERE id=?",
                            (str(e)[:400], time.time(), time.time(), tid),
                        )
                        if _is_risk_error(e):
                            consec_risk += 1
                            if consec_risk >= 2:
                                alert_msg = "🚨 目录整理连续触发 2 次 115 风控拦截，已立即安全熔断并恢复主程序队列！"
                                logstream.error("organize", alert_msg)
                                raise _RiskCircuitBreak("连续触发 2 次 115 风控限制，目录整理已安全熔断")
                        else:
                            consec_risk = 0

                    if idx < total:
                        await _pacing_sleep(settings)
                        await _interruptible_batch_rest(settings, write_ops, "organize")

                if settings.get("media_clean_empty_dirs", True) and moved_parent_cids:
                    removed_dirs = await client.run_pan_thread(_clean_empty_dirs_sync, moved_parent_cids, protect_cids)
                    if removed_dirs:
                        logstream.info("organize", f"🧹 已清理 {removed_dirs} 个已搬空的旧目录")

                now = time.time()
                for r in rules:
                    await asyncio.to_thread(
                        db.execute,
                        "UPDATE media_rules SET last_run_at=?, last_count=? WHERE id=?",
                        (now, rule_counts.get(int(r["id"]), 0), r["id"]),
                    )
                await asyncio.to_thread(db.set_setting, "media_organize_last_run", now)

                logstream.info("organize", f"✅ 目录整理完成：成功 {done_cnt}，跳过 {skip_cnt}，失败 {fail_cnt}")
                result_payload = {
                    "ok": True, "batch_id": batch_id, "total": total,
                    "done": done_cnt, "skipped": skip_cnt, "failed": fail_cnt,
                }

    except _RiskCircuitBreak as rc:
        outcome = "risk_aborted"
        outcome_msg = str(rc)
        logstream.error("organize", f"🛑 {rc}")
        result_payload = {
            "ok": False, "risk_aborted": True, "msg": str(rc), "batch_id": batch_id,
            "done": done_cnt, "skipped": skip_cnt, "failed": fail_cnt,
        }
    except MediaCancelled as mc:
        outcome = "cancelled"
        outcome_msg = str(mc)
        logstream.warn("organize", f"⏸ {mc}")
        result_payload = {
            "ok": False, "cancelled": True, "msg": str(mc), "batch_id": batch_id,
            "done": done_cnt, "skipped": skip_cnt, "failed": fail_cnt,
        }
    except Exception as e:
        outcome = "error"
        outcome_msg = str(e)
        logstream.error("organize", f"目录整理异常: {logstream.exc(e)}")
        result_payload = {"ok": False, "msg": str(e), "batch_id": batch_id}
    finally:
        if _ACTIVE_TASK_REF.get("organize") is asyncio.current_task():
            _ACTIVE_TASK_REF["organize"] = None
        transfer_gate.clear_cancel()
        await asyncio.to_thread(transfer_gate.resume_all)

    elapsed_sec = int(round(time.monotonic() - started_at))
    tg_text = _build_organize_tg_report(
        outcome=outcome,
        trigger=trigger,
        batch_id=batch_id,
        elapsed_sec=elapsed_sec,
        rule_cnt=rule_cnt,
        total_candidates=total,
        done_cnt=done_cnt,
        skip_cnt=skip_cnt,
        fail_cnt=fail_cnt,
        removed_dirs=removed_dirs,
        done_items=done_items,
        fail_items=fail_items,
        error_msg=outcome_msg,
        system_running=is_system_running(),
    )
    if tg_text:
        jobs.enqueue_tg_message(tg_text, gap_sec=1.0)

    return result_payload


# ---------- 刮削落盘与本地元数据 ----------

async def _finalize_scraped_task(
    task_row: dict,
    cand_detail: dict,
    kind: str,
    settings: dict,
    batch_id: str,
    api_key: str,
    caches: tuple[dict, dict, dict, list],
) -> dict:
    """将已确认 TMDB 身份的单个（或按剧聚合的多集）任务执行 115 规范落盘 + 本地写 `.nfo` / 下载海报（零 115 上传）。"""
    dest_root_cache, subdir_cache, dir_entries_cache, moved_parent_cids = caches
    parsed_dict = json.loads(task_row.get("parsed_json") or "{}")
    pm = ParsedMedia(**{k: v for k, v in parsed_dict.items() if k in ParsedMedia.__dataclass_fields__})

    tmdb_id = int(cand_detail["id"])
    title = str(cand_detail.get("name") or cand_detail.get("title") or task_row.get("selected_title") or pm.title or "").strip()
    date_str = str(cand_detail.get("first_air_date") or cand_detail.get("release_date") or "").strip()
    year = date_str[:4] if len(date_str) >= 4 else str(task_row.get("selected_year") or pm.year or "")

    # 回查 rule 取 dest_root 与配对字幕信息
    rule = await asyncio.to_thread(db.query, "SELECT * FROM media_rules WHERE id=?", (task_row.get("rule_id") or 0,), one=True)
    dest_root = "/" + str((rule["target_dir"] if rule and rule["target_dir"] else task_row["source_dir"])).strip("/")

    tmdb_meta = {"tmdb_id": tmdb_id, "title": title, "year": year, "media_type": kind}

    grouped_snaps = parsed_dict.get("_grouped_items") or []
    if grouped_snaps:
        work_items = grouped_snaps
    else:
        fallback_snap = parsed_dict.get("_item_snapshot") or {
            "file_id": task_row["file_id"],
            "parent_cid": parsed_dict.get("_parent_cid") or task_row["source_cid"],
            "source_cid": task_row["source_cid"],
            "source_dir": task_row["source_dir"],
            "dest_root": dest_root,
            "orig_name": task_row["orig_name"],
            "orig_path": task_row["orig_path"],
            "size": int(parsed_dict.get("_size") or 0),
            "sha1": str(parsed_dict.get("_sha1") or ""),
            "is_bdmv": bool(pm.is_bdmv),
            "subtitles": parsed_dict.get("_subtitles") or [],
            "parsed": parsed_dict,
        }
        work_items = [fallback_snap]

    season_nums: set[int] = set()
    ep_items: list[tuple[int, int, str]] = []
    first_show_folder = ""
    last_placement: dict = {"status": "done", "dest_path": "", "dest_name": "", "reason": ""}
    first_ok_placement: dict | None = None
    placed_paths: list[str] = []
    subtitles_moved = 0
    ok_cnt = 0
    ep_fail_cnt = 0
    consec_ep_risk = 0
    ep_write_ops = 0
    in_place_cnt = 0
    last_err = ""

    for g_idx, g_snap in enumerate(work_items):
        _check_not_cancelled()
        g_pdict = g_snap.get("parsed") if isinstance(g_snap.get("parsed"), dict) else parsed_dict
        g_pm = ParsedMedia(**{k: v for k, v in g_pdict.items() if k in ParsedMedia.__dataclass_fields__})
        if kind == "tv" and g_pm.episode is not None:
            mapped_s, mapped_e, mapped_end = remap_absolute_episode(
                g_pm.season, g_pm.episode, g_pm.episode_end, cand_detail
            )
            base_s = 1 if g_pm.season is None else g_pm.season
            if (mapped_s, mapped_e, mapped_end) != (base_s, g_pm.episode, g_pm.episode_end):
                g_pm = ParsedMedia(**{**g_pm.__dict__, "season": mapped_s, "episode": mapped_e, "episode_end": mapped_end})

        rel_dir, target_name = format_media_target(g_pm, settings, tmdb_meta=tmdb_meta)
        if not first_show_folder:
            first_show_folder = rel_dir.split("/")[0] if "/" in rel_dir else rel_dir

        item_payload = dict(g_snap)
        item_payload.setdefault("source_cid", task_row["source_cid"])
        item_payload.setdefault("source_dir", task_row["source_dir"])
        item_payload.setdefault("parent_cid", parsed_dict.get("_parent_cid") or task_row["source_cid"])
        item_payload.setdefault("size", int(parsed_dict.get("_size") or 0))
        item_payload.setdefault("sha1", str(parsed_dict.get("_sha1") or ""))
        item_payload.setdefault("is_bdmv", bool(g_pm.is_bdmv))
        item_payload.setdefault("subtitles", [])
        item_payload["parsed"] = g_pm
        item_payload["dest_root"] = dest_root

        try:
            placement = await client.run_pan_thread(
                _apply_media_placement_sync,
                item_payload, rel_dir, target_name, settings, batch_id, int(task_row["id"]), tmdb_id,
                dest_root_cache, subdir_cache, dir_entries_cache, moved_parent_cids,
            )
        except Exception as ge:
            if len(work_items) == 1:
                raise
            ep_fail_cnt += 1
            last_err = str(ge)
            logstream.warn("scrape", f"剧集分组落盘失败 {g_snap.get('orig_name', '')}: {ge}")
            if _is_risk_error(ge):
                consec_ep_risk += 1
                if consec_ep_risk >= 2:
                    logstream.error("scrape", "🚨 刮削批量落盘连续触发 2 次 115 风控拦截，已立即安全熔断并恢复主程序队列！")
                    raise _RiskCircuitBreak("连续触发 115 风控限制，刮削落盘已安全熔断")
            else:
                consec_ep_risk = 0
            continue

        consec_ep_risk = 0
        last_placement = placement
        if first_ok_placement is None:
            first_ok_placement = placement
        if placement.get("dest_path"):
            dp = str(placement["dest_path"]).rstrip("/")
            if dp and dp not in placed_paths:
                placed_paths.append(dp)
        subtitles_moved += int(placement.get("subtitles_moved") or 0)
        ok_cnt += 1
        if placement.get("status") == "done":
            ep_write_ops += 1
        elif placement.get("in_place"):
            in_place_cnt += 1

        if kind == "tv":
            season_num = 0 if g_pm.is_special else (g_pm.season if g_pm.season is not None else 1)
            season_nums.add(season_num)
            if g_pm.episode is not None:
                ep_stem = os.path.splitext(placement.get("dest_name") or target_name)[0]
                ep_items.append((season_num, g_pm.episode, ep_stem))

        if g_idx + 1 < len(work_items) and placement.get("status") == "done":
            await _pacing_sleep(settings)
            await _interruptible_batch_rest(settings, ep_write_ops, "scrape")

    if len(work_items) > 1 and ok_cnt == 0 and ep_fail_cnt > 0:
        raise RuntimeError(last_err or "同剧分组落盘全部失败")

    rep_place = first_ok_placement or last_placement
    conflict_skipped_all = (ep_write_ops == 0 and in_place_cnt == 0 and rep_place.get("status") == "skipped")
    # 本地生成 .nfo 与下载海报（零 115 上传请求；仅对已移动或已在规范位置的影片落盘元数据）
    local_dir_str = ""
    has_nfo = False
    has_poster = False
    if not conflict_skipped_all and (settings.get("media_scrape_write_local_nfo", True) or settings.get("media_scrape_download_poster", True)):
        if not first_show_folder:
            rel0, _ = format_media_target(pm, settings, tmdb_meta=tmdb_meta)
            first_show_folder = rel0.split("/")[0] if "/" in rel0 else rel0
        meta_dir = compute_local_meta_dir(first_show_folder, str(settings.get("media_local_meta_dir") or ""))
        meta_res = await asyncio.to_thread(
            save_local_metadata,
            meta_dir, cand_detail, kind,
            season_nums=sorted(season_nums) if kind == "tv" and season_nums else ([1] if kind == "tv" else None),
            episode_items=ep_items,
            api_key=api_key,
            write_nfo=bool(settings.get("media_scrape_write_local_nfo", True)),
            download_poster=bool(settings.get("media_scrape_download_poster", True)),
        )
        local_dir_str = meta_res.get("meta_dir", "")
        saved_meta_files = list(meta_res.get("files") or [])
        has_nfo = any(str(p).lower().endswith(".nfo") for p in saved_meta_files)
        has_poster = bool(meta_res.get("poster_local")) or any(
            str(p).lower().endswith((".jpg", ".jpeg", ".png")) for p in saved_meta_files
        )

    now = time.time()
    final_status = "skipped" if conflict_skipped_all else (
        "done" if rep_place["status"] in ("done", "skipped") else rep_place["status"]
    )
    dest_name_disp = rep_place["dest_name"]
    reason_disp = rep_place.get("reason", "")
    if len(work_items) > 1:
        dest_name_disp = f"{rep_place['dest_name']} 等{ok_cnt}集"
        if ep_fail_cnt > 0:
            reason_disp = f"共 {len(work_items)} 集：成功 {ok_cnt}，失败 {ep_fail_cnt}"
        elif not reason_disp:
            reason_disp = f"同剧合并落盘共 {ok_cnt} 集"

    await asyncio.to_thread(
        db.execute,
        "UPDATE media_tasks SET status=?, media_type=?, target_path=?, target_name=?, selected_tmdb_id=?, "
        "selected_kind=?, selected_title=?, selected_year=?, local_meta_dir=?, error=?, updated_at=?, finished_at=? WHERE id=?",
        (
            final_status, kind, rep_place["dest_path"], dest_name_disp, tmdb_id, kind,
            title, year, local_dir_str, reason_disp, now, now, task_row["id"],
        ),
    )
    return {
        "status": final_status,
        "dest_path": rep_place["dest_path"],
        "dest_paths": placed_paths,
        "dest_name": dest_name_disp,
        "local_meta_dir": local_dir_str,
        "title": title,
        "year": year,
        "tmdb_id": tmdb_id,
        "kind": kind,
        "ok_count": max(1, ok_cnt),
        "subtitles_moved": subtitles_moved,
        "has_nfo": has_nfo,
        "has_poster": has_poster,
        "is_bdmv": bool(pm.is_bdmv),
    }


# ---------- 网页端待确认暂存、撤回、忽略与提交队列 ----------


def stage_task_selection(
    task_id: int,
    cand_idx: int | None = None,
    manual_cand: dict | None = None,
    source: str = "web",
) -> dict:
    """网页端原子『暂存候选』操作（只记录选定候选为 `staged`，绝不提交或动 115）。"""
    now = time.time()

    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone()
        if not row:
            return {"ok": False, "code": "not_found", "msg": "该任务已不存在"}
        r = dict(row)
        status = str(r.get("status") or "")

        if status not in ("need_confirm", "staged"):
            return {
                "ok": False,
                "code": "locked_status",
                "msg": f"该条已处于「{STATUS_ZH.get(status, status)}」状态，不可再更改",
                "task": r,
            }

        if manual_cand:
            chosen = manual_cand
        else:
            cands = json.loads(r.get("candidates_json") or "[]")
            if cand_idx is None or cand_idx < 0 or cand_idx >= len(cands):
                return {"ok": False, "code": "bad_index", "msg": "候选项序号无效", "task": r}
            chosen = cands[cand_idx]

        new_tmdb_id = int(chosen["tmdb_id"])
        conn.execute(
            "UPDATE media_tasks SET status='staged', selected_tmdb_id=?, selected_kind=?, selected_title=?, "
            "selected_year=?, selected_poster=?, select_source=?, updated_at=? WHERE id=?",
            (
                new_tmdb_id,
                str(chosen.get("kind") or "movie"),
                str(chosen.get("title") or ""),
                str(chosen.get("year") or ""),
                str(chosen.get("poster_path") or ""),
                source,
                now,
                int(task_id),
            ),
        )
        updated = dict(conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone())

    return {"ok": True, "code": "staged", "msg": f"已暂存：{updated['selected_title']}（等待提交队列）", "task": updated}


def unstage_task_selection(task_id: int, source: str = "web") -> dict:
    """撤销暂存：从 `staged` 安全回退到 `need_confirm`。"""
    now = time.time()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone()
        if not row:
            return {"ok": False, "code": "not_found", "msg": "该任务已不存在"}
        r = dict(row)
        if r["status"] != "staged":
            return {"ok": False, "code": "locked_status", "msg": f"当前状态为「{STATUS_ZH.get(r['status'], r['status'])}」，无法撤销暂存", "task": r}
        conn.execute(
            "UPDATE media_tasks SET status='need_confirm', selected_tmdb_id=NULL, selected_kind='', "
            "selected_title='', selected_year='', selected_poster='', select_source='', updated_at=? WHERE id=?",
            (now, int(task_id)),
        )
        updated = dict(conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone())

    return {"ok": True, "code": "need_confirm", "msg": "已撤销暂存，恢复待确认", "task": updated}


def ignore_task(task_id: int, source: str = "web") -> dict:
    """将待确认或已暂存条目置为 `ignored`。"""
    now = time.time()
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone()
        if not row:
            return {"ok": False, "code": "not_found", "msg": "该任务已不存在"}
        r = dict(row)
        if r["status"] not in ("need_confirm", "staged"):
            return {"ok": False, "code": "locked_status", "msg": f"该条已处于「{STATUS_ZH.get(r['status'], r['status'])}」状态", "task": r}
        conn.execute(
            "UPDATE media_tasks SET status='ignored', select_source=?, updated_at=?, finished_at=? WHERE id=?",
            (source, now, now, int(task_id)),
        )
        updated = dict(conn.execute("SELECT * FROM media_tasks WHERE id=?", (int(task_id),)).fetchone())

    return {"ok": True, "code": "ignored", "msg": "已忽略此条目", "task": updated}


def commit_staged_tasks(
    task_ids: list[int] | None = None,
    tg_batch_id: str = "",
    source: str = "web",
) -> dict:
    """统一『🚀 提交队列』按钮逻辑：受顶部 running 开关与四方互斥保护，将 `staged -> queued` 并唤醒串行刮削执行。"""
    if not is_system_running():
        return {"ok": False, "code": "system_stopped", "msg": "系统顶部运行开关处于「已停止」，禁止提交执行！已为您保留已选条目。"}

    active_mode = transfer_gate.get_active_mode()
    if active_mode in ("organize", "auto_move"):
        mode_zh = "目录整理" if active_mode == "organize" else "定时文件整理"
        return {"ok": False, "code": "mutex_busy", "msg": f"当前正在执行【{mode_zh}】，互斥保护下无法同时启动媒体刮削，请待结束后再提交"}

    now = time.time()
    committed_rows: list[dict] = []

    with db.transaction() as conn:
        sql = "SELECT * FROM media_tasks WHERE status='staged' AND task_kind='scrape'"
        params: list = []
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            sql += f" AND id IN ({placeholders})"
            params.extend(int(x) for x in task_ids)
        elif tg_batch_id:
            sql += " AND tg_batch_id=?"
            params.append(str(tg_batch_id))
        sql += " ORDER BY id ASC"
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        if not rows:
            return {"ok": False, "code": "empty", "msg": "暂无「已选待提交」条目，请先点选至少一部影视候选"}

        ids_to_commit = [int(r["id"]) for r in rows]
        ph = ",".join("?" for _ in ids_to_commit)
        conn.execute(
            f"UPDATE media_tasks SET status='queued', updated_at=? WHERE id IN ({ph}) AND status='staged'",
            tuple([now] + ids_to_commit),
        )
        committed_rows = [dict(r) for r in conn.execute(f"SELECT * FROM media_tasks WHERE id IN ({ph})", tuple(ids_to_commit)).fetchall()]

    return {
        "ok": True,
        "code": "committed",
        "committed_count": len(committed_rows),
        "committed_ids": [int(r["id"]) for r in committed_rows],
        "msg": f"已提交 {len(committed_rows)} 条进入顺序执行队列（受防风控队列串行调度）",
    }


def schedule_commit_runner_if_idle() -> bool:
    """提交队列后，若当前刮削循环未运行且门禁空闲，则在当前事件循环中拉起 `commit_only` 消费循环。"""
    global _COMMIT_WAKEUP_REQUESTED
    t = _ACTIVE_TASK_REF.get("scrape")
    if t and not t.done():
        _COMMIT_WAKEUP_REQUESTED = True
        return False
    if transfer_gate.is_exclusive_busy():
        if transfer_gate.get_active_mode() == "scrape":
            _COMMIT_WAKEUP_REQUESTED = True
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _COMMIT_WAKEUP_REQUESTED = False
    _ACTIVE_TASK_REF["scrape"] = loop.create_task(execute_scrape_queue(mode="commit_only"))
    return True


def _placed_to_report_item(placed: dict, fallback_orig: str) -> dict:
    """将 `_finalize_scraped_task` 返回结构转换为 `_format_media_detail_lines` 条目。"""
    return {
        "media_type": str(placed.get("kind") or "movie"),
        "title": str(placed.get("title") or fallback_orig or "未命名媒体"),
        "year": str(placed.get("year") or ""),
        "tmdb_id": placed.get("tmdb_id"),
        "count": max(1, int(placed.get("ok_count") or 1)),
        "sub_count": int(placed.get("subtitles_moved") or 0),
        "is_bdmv": bool(placed.get("is_bdmv")),
        "has_nfo": bool(placed.get("has_nfo")),
        "has_poster": bool(placed.get("has_poster")),
        "dest_path": str(placed.get("dest_path") or ""),
        "dest_paths": list(placed.get("dest_paths") or []),
        "dest_name": str(placed.get("dest_name") or ""),
    }


# ---------- 模块 B：媒体刮削严格单队列引擎 ----------

async def execute_scrape_queue(mode: str = "scan_and_run", rule_ids: list[int] | None = None) -> dict:
    """模块 B：媒体刮削引擎（`scan_and_run` 扫描+刮削，或 `commit_only` 消费人工已确认队列）。"""
    global _COMMIT_WAKEUP_REQUESTED
    if not is_system_running():
        return {"ok": False, "msg": "系统处于停止状态，请先点击顶栏「运行」启动系统"}

    settings = await asyncio.to_thread(get_media_settings)
    api_key = str(settings.get("tmdb_api_key") or "").strip()
    if not api_key:
        return {"ok": False, "msg": "请先在「系统设置」填写有效的 TMDB API Key 再运行媒体刮削"}

    try:
        conf_th = float(settings.get("media_scrape_confidence_threshold", 85) or 85)
    except (TypeError, ValueError):
        conf_th = 85.0
    try:
        margin_th = float(settings.get("media_scrape_margin_threshold", 12) or 12)
    except (TypeError, ValueError):
        margin_th = 12.0

    lock = _get_lock("scrape")
    if lock.locked():
        return {"ok": False, "msg": "媒体刮削任务正在执行中，请勿重复发起"}

    ok, busy_msg = transfer_gate.try_begin_exclusive("scrape")
    if not ok:
        return {"ok": False, "msg": busy_msg}

    started_at = time.monotonic()
    run_batch_id = f"scr_{uuid.uuid4().hex[:10]}"
    auto_cnt = 0
    confirm_cnt = 0
    fail_cnt = 0
    skip_cnt = 0
    rule_cnt = 0
    total = 0
    removed_dirs = 0
    auto_items: list[dict] = []
    pending_map: dict[int, dict] = {}
    fail_items: list[tuple[str, str]] = []
    outcome = "done"
    outcome_msg = ""
    result_payload: dict = {}
    rerun_committed = False

    try:
        async with lock:
            transfer_gate.clear_cancel()
            transfer_gate.set_media_progress({"mode": "scrape", "phase": "draining", "current": 0, "total": 0, "label": "等待转存队列消化"})
            drained = await transfer_gate.prepare_and_drain_transfers(mode="scrape")
            if drained is False:
                raise MediaCancelled("排空阶段收到中断或停止信号，媒体刮削安全中止")
            _check_not_cancelled()

            dest_root_cache: dict[str, int] = {}
            subdir_cache: dict[tuple[int, str], int] = {}
            dir_entries_cache: dict[int, list[dict]] = {}
            moved_parent_cids: list[str] = []
            caches = (dest_root_cache, subdir_cache, dir_entries_cache, moved_parent_cids)
            protect_cids: set[str] = set()
            consec_risk = 0
            write_ops = 0

            no_rules_exit = False
            if mode == "scan_and_run":
                transfer_gate.set_media_progress({"mode": "scrape", "phase": "scanning", "current": 0, "total": 0, "label": "扫描待刮削媒体目录"})
                rules, items = await client.run_pan_thread(_scan_rules_sync, rule_ids, "scrape_enabled", settings)
                rule_cnt = len(rules)
                if not rules:
                    no_rules_exit = True
                    result_payload = {"ok": True, "batch_id": run_batch_id, "auto": 0, "need_confirm": 0, "failed": 0, "skipped": 0, "msg": "没有启用的刮削规则"}
                else:
                    total = len(items)
                    logstream.info("scrape", f"▶ 开始媒体刮削（扫描 {len(rules)} 条规则，候选 {total} 项）")
                    show_decision_cache: dict[tuple, tuple[str, list[dict]]] = {}
                    show_confirm_tid: dict[tuple, int] = {}

                    pending_rows = await asyncio.to_thread(
                        db.query,
                        "SELECT id, rule_id, media_type, file_id, status, parsed_json FROM media_tasks "
                        "WHERE task_kind='scrape' AND status IN ('need_confirm','staged','queued') ORDER BY id ASC",
                    )
                    pending_file_ids: set[str] = set()
                    for pr in pending_rows:
                        if pr["file_id"]:
                            pending_file_ids.add(str(pr["file_id"]))
                        try:
                            pj = json.loads(pr["parsed_json"] or "{}")
                            for gi in (pj.get("_grouped_items") or []):
                                if gi.get("file_id"):
                                    pending_file_ids.add(str(gi["file_id"]))
                            if (
                                pr["status"] == "need_confirm"
                                and pr["media_type"] == "tv"
                                and pj.get("title")
                                and not pj.get("tmdb_id")
                                and "_grouped_items" in pj
                            ):
                                gkey = (
                                    int(pr["rule_id"] or 0),
                                    normalize_title(str(pj["title"]), fuzzy_t2s=True),
                                    str(pj.get("year") or ""),
                                )
                                show_confirm_tid.setdefault(gkey, int(pr["id"]))
                        except Exception:
                            pass

                    for idx, it in enumerate(items, 1):
                        _check_not_cancelled()
                        pm: ParsedMedia = it["parsed"]
                        protect_cids.add(str(it.get("source_cid") or ""))
                        transfer_gate.set_media_progress({
                            "mode": "scrape", "phase": "running",
                            "current": idx, "total": total, "label": f"正在匹配: {it['orig_name']}",
                        })

                        # 如果同一 file_id 已处于待确认/暂存/排队（含剧集聚合分组内），避免重复生成孤立条目
                        if str(it["file_id"]) in pending_file_ids:
                            skip_cnt += 1
                            continue

                        show_group_key = None
                        if pm.media_type == "tv" and pm.title and not pm.tmdb_id:
                            show_group_key = (int(it["rule_id"]), normalize_title(pm.title, fuzzy_t2s=True), str(pm.year or ""))

                        item_snap = {
                            "file_id": it["file_id"],
                            "parent_cid": it["parent_cid"],
                            "source_cid": it["source_cid"],
                            "source_dir": it["source_dir"],
                            "dest_root": it.get("dest_root") or "",
                            "orig_name": it["orig_name"],
                            "orig_path": it["orig_path"],
                            "size": it["size"],
                            "sha1": it["sha1"],
                            "is_bdmv": bool(pm.is_bdmv),
                            "subtitles": it["subtitles"],
                            "parsed": pm.to_dict(),
                        }

                        if show_group_key and show_group_key in show_confirm_tid:
                            grp_tid = show_confirm_tid[show_group_key]
                            grp_row = await asyncio.to_thread(
                                db.query, "SELECT parsed_json FROM media_tasks WHERE id=?", (grp_tid,), one=True
                            )
                            if grp_row:
                                grp_p = json.loads(grp_row["parsed_json"] or "{}")
                                grp_items = list(grp_p.get("_grouped_items") or [])
                                grp_items.append(item_snap)
                                grp_p["_grouped_items"] = grp_items
                                await asyncio.to_thread(
                                    db.execute,
                                    "UPDATE media_tasks SET parsed_json=?, updated_at=? WHERE id=?",
                                    (json.dumps(grp_p, ensure_ascii=False), time.time(), grp_tid),
                                )
                                pending_file_ids.add(str(it["file_id"]))
                                if grp_tid in pending_map:
                                    pending_map[grp_tid]["ep_count"] = len(grp_items)
                                continue

                        parsed_snap = pm.to_dict()
                        parsed_snap["_parent_cid"] = it["parent_cid"]
                        parsed_snap["_size"] = it["size"]
                        parsed_snap["_sha1"] = it["sha1"]
                        parsed_snap["_subtitles"] = it["subtitles"]
                        if show_group_key:
                            parsed_snap["_grouped_items"] = [item_snap]
                        now = time.time()

                        tid = await asyncio.to_thread(
                            db.insert,
                            "INSERT INTO media_tasks(task_kind,rule_id,media_type,source_dir,source_cid,file_id,orig_name,orig_path,"
                            "parsed_json,status,batch_id,created_at,updated_at) VALUES('scrape',?,?,?,?,?,?,?,?,?,?,?,?)",
                            (it["rule_id"], pm.media_type, it["source_dir"], it["source_cid"], it["file_id"],
                             it["orig_name"], it["orig_path"], json.dumps(parsed_snap, ensure_ascii=False),
                             "running", run_batch_id, now, now),
                        )

                        # 同剧集同标题缓存一次 TMDB 候选打分，减少网络请求同时全剧保持同一识别基准
                        cache_key = None
                        if pm.media_type == "tv" and pm.title and not pm.tmdb_id:
                            cache_key = (normalize_title(pm.title, fuzzy_t2s=True), pm.year, it["rule_media_type"])

                        if cache_key and cache_key in show_decision_cache:
                            decision, scored = show_decision_cache[cache_key]
                        else:
                            scored = await asyncio.to_thread(recall_and_score, pm, api_key, it["rule_media_type"], it.get("ep_stats"))
                            decision, scored = decide_match(scored, conf_th, margin_th)
                            if cache_key and scored:
                                show_decision_cache[cache_key] = (decision, scored)

                        compact = compact_candidates(scored)
                        top1 = compact[0] if compact else None

                        if decision == "auto" and top1:
                            detail = (scored[0].get("detail") if scored else None) or await asyncio.to_thread(
                                get_tmdb_detail, top1["kind"], int(top1["tmdb_id"]), api_key
                            )
                            if detail:
                                task_row = await asyncio.to_thread(db.query, "SELECT * FROM media_tasks WHERE id=?", (tid,), one=True)
                                try:
                                    placed = await _finalize_scraped_task(dict(task_row), detail, top1["kind"], settings, run_batch_id, api_key, caches)
                                    consec_risk = 0
                                    if placed.get("status") == "skipped":
                                        skip_cnt += 1
                                    else:
                                        auto_cnt += 1
                                        write_ops += 1
                                        auto_items.append(_placed_to_report_item(placed, it["orig_name"]))
                                    await asyncio.to_thread(
                                        db.execute,
                                        "UPDATE media_tasks SET confidence=?, candidates_json=? WHERE id=?",
                                        (float(top1.get("score") or 0), json.dumps(compact, ensure_ascii=False), tid),
                                    )
                                    if idx < total and placed.get("status") != "skipped":
                                        await _pacing_sleep(settings)
                                        await _interruptible_batch_rest(settings, write_ops, "scrape")
                                    continue
                                except _RiskCircuitBreak:
                                    fail_cnt += 1
                                    fail_items.append((it["orig_name"], "触发 115 风控熔断"))
                                    await asyncio.to_thread(
                                        db.execute,
                                        "UPDATE media_tasks SET status='failed', error='触发 115 风控熔断', updated_at=?, finished_at=? WHERE id=?",
                                        (time.time(), time.time(), tid),
                                    )
                                    raise
                                except MediaCancelled:
                                    await asyncio.to_thread(
                                        db.execute,
                                        "UPDATE media_tasks SET status='failed', error='已安全中断', updated_at=?, finished_at=? WHERE id=?",
                                        (time.time(), time.time(), tid),
                                    )
                                    raise
                                except Exception as fe:
                                    fail_cnt += 1
                                    fail_items.append((it["orig_name"], str(fe)))
                                    await asyncio.to_thread(
                                        db.execute,
                                        "UPDATE media_tasks SET status='failed', error=?, updated_at=?, finished_at=? WHERE id=?",
                                        (str(fe)[:400], time.time(), time.time(), tid),
                                    )
                                    if _is_risk_error(fe):
                                        consec_risk += 1
                                        if consec_risk >= 2:
                                            logstream.error("scrape", "🚨 媒体刮削连续触发 2 次 115 风控拦截，已立即安全熔断并恢复主程序队列！")
                                            raise _RiskCircuitBreak("连续触发 115 风控限制，媒体刮削已安全熔断")
                                    else:
                                        consec_risk = 0
                                    continue

                        # 默认 0 秒直接跳过：立即挂起为 need_confirm 并附带 Top 候选，交网页「待确认列表」处理
                        confirm_cnt += 1
                        pending_file_ids.add(str(it["file_id"]))
                        if show_group_key:
                            show_confirm_tid[show_group_key] = tid
                        pending_map[tid] = {
                            "title": pm.title or it["orig_name"],
                            "year": str(pm.year or ""),
                            "orig_name": it["orig_name"],
                            "media_type": "tv" if pm.media_type == "tv" else "movie",
                            "ep_count": 1,
                            "top1": top1,
                        }
                        await asyncio.to_thread(
                            db.execute,
                            "UPDATE media_tasks SET status='need_confirm', confidence=?, candidates_json=?, "
                            "updated_at=? WHERE id=?",
                            (
                                float(top1.get("score") or 0) if top1 else 0.0,
                                json.dumps(compact, ensure_ascii=False),
                                time.time(),
                                tid,
                            ),
                        )

            # 消费全部状态为 queued 的待执行刮削任务（含人工提交队列与重启恢复队列）
            if not no_rules_exit:
                while True:
                    _check_not_cancelled()
                    _COMMIT_WAKEUP_REQUESTED = False
                    q_row = await asyncio.to_thread(
                        db.query,
                        "SELECT * FROM media_tasks WHERE task_kind='scrape' AND status='queued' ORDER BY id ASC LIMIT 1",
                        one=True,
                    )
                    if not q_row:
                        break
                    qr = dict(q_row)
                    protect_cids.add(str(qr.get("source_cid") or ""))
                    await asyncio.to_thread(db.execute, "UPDATE media_tasks SET status='running', updated_at=? WHERE id=?", (time.time(), qr["id"]))
                    transfer_gate.set_media_progress({
                        "mode": "scrape", "phase": "running",
                        "current": auto_cnt + 1, "total": auto_cnt + 1, "label": f"执行已确认: {qr.get('selected_title') or qr['orig_name']}",
                    })
                    try:
                        sel_id = int(qr.get("selected_tmdb_id") or 0)
                        sel_kind = str(qr.get("selected_kind") or qr.get("media_type") or "movie")
                        if not sel_id:
                            raise RuntimeError("未指定有效 TMDB ID")
                        det = await asyncio.to_thread(get_tmdb_detail, sel_kind, sel_id, api_key)
                        if not det:
                            raise RuntimeError(f"拉取 TMDB/{sel_kind}/{sel_id} 详情失败")
                        placed = await _finalize_scraped_task(qr, det, sel_kind, settings, run_batch_id, api_key, caches)
                        consec_risk = 0
                        if placed.get("status") == "skipped":
                            skip_cnt += 1
                        else:
                            auto_cnt += 1
                            write_ops += 1
                            auto_items.append(_placed_to_report_item(placed, str(qr.get("orig_name") or "")))
                            await _pacing_sleep(settings)
                            await _interruptible_batch_rest(settings, write_ops, "scrape")
                    except _RiskCircuitBreak:
                        fail_cnt += 1
                        fail_items.append((str(qr.get("selected_title") or qr.get("orig_name") or ""), "触发 115 风控熔断"))
                        await asyncio.to_thread(db.execute, "UPDATE media_tasks SET status='queued', updated_at=? WHERE id=?", (time.time(), qr["id"]))
                        raise
                    except MediaCancelled:
                        await asyncio.to_thread(db.execute, "UPDATE media_tasks SET status='queued', updated_at=? WHERE id=?", (time.time(), qr["id"]))
                        raise
                    except Exception as qe:
                        fail_cnt += 1
                        fail_items.append((str(qr.get("selected_title") or qr.get("orig_name") or ""), str(qe)))
                        await asyncio.to_thread(
                            db.execute,
                            "UPDATE media_tasks SET status='failed', error=?, updated_at=?, finished_at=? WHERE id=?",
                            (str(qe)[:400], time.time(), time.time(), qr["id"]),
                        )
                        if _is_risk_error(qe):
                            consec_risk += 1
                            if consec_risk >= 2:
                                logstream.error("scrape", "🚨 刮削已提交队列连续触发 2 次 115 风控拦截，已安全熔断并恢复主程序！")
                                raise _RiskCircuitBreak("连续触发 115 风控限制，刮削队列已安全熔断")
                        else:
                            consec_risk = 0

                if settings.get("media_clean_empty_dirs", True) and moved_parent_cids:
                    removed_dirs = await client.run_pan_thread(_clean_empty_dirs_sync, moved_parent_cids, protect_cids)
                    if removed_dirs:
                        logstream.info("scrape", f"🧹 已清理 {removed_dirs} 个已搬空的旧目录")

                await asyncio.to_thread(db.set_setting, "media_scrape_last_run", time.time())
                logstream.info("scrape", f"✅ 媒体刮削批次结束：完成 {auto_cnt}，待确认 {confirm_cnt}，失败 {fail_cnt}")
                result_payload = {
                    "ok": True,
                    "batch_id": run_batch_id,
                    "auto": auto_cnt,
                    "need_confirm": confirm_cnt,
                    "failed": fail_cnt,
                    "skipped": skip_cnt,
                }

    except _RiskCircuitBreak as rc:
        outcome = "risk_aborted"
        outcome_msg = str(rc)
        logstream.error("scrape", f"🛑 {rc}")
        result_payload = {"ok": False, "risk_aborted": True, "msg": str(rc), "auto": auto_cnt, "need_confirm": confirm_cnt, "failed": fail_cnt}
    except MediaCancelled as mc:
        outcome = "cancelled"
        outcome_msg = str(mc)
        logstream.warn("scrape", f"⏸ {mc}")
        result_payload = {"ok": False, "cancelled": True, "msg": str(mc), "auto": auto_cnt, "need_confirm": confirm_cnt, "failed": fail_cnt}
    except Exception as e:
        outcome = "error"
        outcome_msg = str(e)
        logstream.error("scrape", f"媒体刮削异常: {logstream.exc(e)}")
        result_payload = {"ok": False, "msg": str(e)}
    finally:
        # 主程序转存与云下载先于通知立即恢复运行
        transfer_gate.clear_cancel()
        await asyncio.to_thread(transfer_gate.resume_all)
        rerun_committed = bool(_COMMIT_WAKEUP_REQUESTED and outcome == "done" and is_system_running())
        _COMMIT_WAKEUP_REQUESTED = False
        if _ACTIVE_TASK_REF.get("scrape") is asyncio.current_task():
            _ACTIVE_TASK_REF["scrape"] = None

    elapsed_sec = int(round(time.monotonic() - started_at))
    tg_text = _build_scrape_tg_report(
        mode=mode,
        outcome=outcome,
        elapsed_sec=elapsed_sec,
        rule_cnt=rule_cnt,
        total_candidates=total,
        auto_cnt=auto_cnt,
        confirm_cnt=confirm_cnt,
        skip_cnt=skip_cnt,
        fail_cnt=fail_cnt,
        removed_dirs=removed_dirs,
        auto_items=auto_items,
        pending_items=list(pending_map.values()),
        fail_items=fail_items,
        error_msg=outcome_msg,
        system_running=is_system_running(),
    )
    if tg_text:
        jobs.enqueue_tg_message(tg_text, gap_sec=1.0)

    if rerun_committed:
        try:
            asyncio.get_running_loop().call_soon(schedule_commit_runner_if_idle)
        except RuntimeError:
            pass

    return result_payload


# ---------- 操作账本撤销（安全回退） ----------

def _undo_records_sync(records: list[dict]) -> tuple[int, int]:
    """同步撤销 `organized_records` 记录（恢复原位置与原文件名；若原目录已被清理则自动重建）。"""
    undone = 0
    failed = 0
    recreated_cids: dict[str, int] = {}
    for rec in records:
        try:
            fid = str(rec["file_id"])
            orig_cid = int(str(rec.get("parent_cid") or "0") or 0)
            dest_cid = int(str(rec.get("dest_cid") or "0") or 0)
            orig_name = str(rec["orig_name"])
            dest_name = str(rec["dest_name"])
            orig_parent_dir = posixpath.dirname(str(rec.get("orig_path") or ""))

            def _ensure_orig_cid() -> int:
                if orig_parent_dir and orig_parent_dir != "/":
                    cid = recreated_cids.get(orig_parent_dir) or int(client.mkdirs(orig_parent_dir))
                    recreated_cids[orig_parent_dir] = cid
                    return cid
                return 0

            if not orig_cid:
                orig_cid = _ensure_orig_cid()
            if not orig_cid:
                raise RuntimeError("缺少原始目录 cid")

            if dest_cid != orig_cid:
                try:
                    client.move_entries([fid], orig_cid)
                except Exception as move_err:
                    # 批末空目录清理可能已删除原目录 cid，按 orig_path 逐级重建后再重试移动
                    recreated = _ensure_orig_cid()
                    if recreated and recreated != orig_cid:
                        orig_cid = recreated
                        client.move_entries([fid], orig_cid)
                    else:
                        raise move_err
            if dest_name != orig_name:
                client.rename_entry(fid, orig_name)
            db.execute("UPDATE organized_records SET undone=1 WHERE id=?", (rec["id"],))
            undone += 1
            time.sleep(0.6 * client.pace_factor())
        except Exception as e:
            failed += 1
            logstream.warn("organize", f"撤销记录 #{rec['id']}({rec['dest_name']}) 失败: {e}")
    return undone, failed


async def undo_organized(batch_id: str = "", record_id: int = 0) -> dict:
    """按单条记录或整个批次安全撤销已移动/重命名的网盘文件。"""
    if transfer_gate.is_exclusive_busy():
        return {"ok": False, "msg": "当前正在执行整理或刮削，请待结束后再执行撤销"}
    if not record_id and not batch_id:
        return {"ok": False, "msg": "请指定需要撤销的记录 ID 或批次 ID"}

    if record_id:
        rows = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM organized_records WHERE id=? AND undone=0", (int(record_id),)
        )]
    else:
        rows = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM organized_records WHERE batch_id=? AND undone=0 ORDER BY id DESC", (batch_id,)
        )]
    if not rows:
        return {"ok": False, "msg": "没有找到可撤销的未回退记录"}

    undone, failed = await client.run_pan_thread(_undo_records_sync, rows)
    return {"ok": undone > 0, "undone": undone, "failed": failed, "msg": f"已撤销 {undone} 条，失败 {failed} 条"}


# ---------- 定时目录整理后台协程 ----------

async def media_organize_loop():
    """后台定时自动目录整理循环（仅在系统 running 且启用定时整理、且四方门禁空闲时触发）。"""
    logstream.info("organize", "定时目录整理协程已启动")
    while True:
        try:
            await asyncio.sleep(60)
            if not is_system_running():
                continue
            if not bool(db.get_setting("media_organize_schedule_enabled", False)):
                continue
            if transfer_gate.is_exclusive_busy():
                continue
            hours = max(1, min(168, int(db.get_setting("media_organize_schedule_hours", 12) or 12)))
            last = float(db.get_setting("media_organize_last_run", 0) or 0)
            if time.time() - last >= hours * 3600:
                await execute_organize_queue(trigger="schedule")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logstream.warn("organize", f"定时目录整理检查异常: {e}")
            await asyncio.sleep(30)
