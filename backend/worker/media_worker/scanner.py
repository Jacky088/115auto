"""115 媒体目录扫描与文件移动/重命名/字幕随动/空目录清理执行层。"""
from __future__ import annotations

import os
import posixpath
import time

from ... import database as db
from ...core import logstream
from ...core.media_parser import (
    ParsedMedia,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    match_subtitle_for_video,
    parse_media_context,
    parse_media_filename,
    resolution_rank,
    sanitize_filename,
)
from ...pan115.client import strip_duplicate_suffix
from .. import scan_index
from .common import (
    _load_db_transfer_hints,
    _match_hint_for_parsed,
    _should_skip_extra,
    _split_rel_and_name,
)

def _mw():
    """延迟获取 media_worker 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import media_worker
    return media_worker


def _scan_rules_sync(
    rule_ids: list[int] | None,
    mode_col: str,
    settings: dict,
    include_tmdb_hint_only: bool = False,
    stats_out: dict | None = None,
    ctx=None,
) -> tuple[list[dict], list[dict]]:
    """同步扫描启用中的 `media_rules` 在 115 上的媒体条目，在 `run_pan_thread` 中调用。

    返回 `(rules, items)`；每个 item 已完成三层 `parse_media_context` 与配套字幕配对。
    stats_out 传入 dict 时累计扫描层跳过计数（非视频/类型互斥/花絮广告过滤/跨规则去重），
    供预览接口向用户解释「为什么某些文件不在清单里」。
    ctx 由异步预览任务传入：目录列表走扫描索引（TTL 内 0 请求、增量提前停止、预算截断）；
    ctx=None 时保持原有 client.walk_media_tree 实时全量扫描。
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
    seen_file_ids: set[str] = set()
    blocked: list[dict] = []

    for rule in rules:
        src_dir = "/" + str(rule["source_dir"] or "").strip("/")
        dest_root = "/" + str(rule.get("target_dir") or src_dir).strip("/")
        rmt = str(rule.get("media_type") or "auto").strip().lower()
        ok_root, root_msg = scan_index.validate_scan_root(src_dir)
        if not ok_root:
            blocked.append({"rule_id": int(rule["id"]), "source_dir": src_dir, "msg": root_msg})
            logstream.warn(mode_col.split("_")[0], f"规则「{rule.get('name') or src_dir}」{root_msg}")
            continue
        try:
            src_cid = _mw().client.resolve_dir_cid(src_dir)
            if not src_cid:
                logstream.warn("organize" if mode_col == "organize_enabled" else "scrape",
                               f"规则「{rule.get('name') or src_dir}」源目录在 115 上不存在，跳过")
                continue
            if ctx is not None:
                tree = scan_index.walk_media_tree(int(src_cid), src_dir, max_depth=4, ctx=ctx)
                if tree.get("truncated"):
                    ctx.truncated = True
            else:
                tree = _mw().client.walk_media_tree(int(src_cid), src_dir, max_depth=4)
        except Exception as e:
            logstream.warn("organize" if mode_col == "organize_enabled" else "scrape",
                           f"扫描规则目录 {src_dir} 失败: {e}")
            continue

        # 统计同父目录下集数情况，用于 TMDB 物理对账与季度识别
        dir_stats: dict[str, dict] = {}
        raw_entries: list[dict] = []

        # 1) BDMV / VIDEO_TS 原盘目录
        for b in tree.get("bdmv_dirs") or []:
            bid = str(b["id"])
            if bid in seen_file_ids:
                continue
            pm = parse_media_context(b["name"], parent_dirs=b.get("parent_dirs") or [], is_dir=True)
            pm.is_bdmv = True
            hint = _match_hint_for_parsed(pm, b.get("parent_dirs") or [], hints_by_title)
            if hint:
                pm = parse_media_context(b["name"], parent_dirs=b.get("parent_dirs") or [], db_hint=hint, is_dir=True)
                pm.is_bdmv = True

            # 规则类型精准互斥过滤：剧集规则严格跳过电影原盘，电影规则严格跳过剧集原盘
            if rmt == "tv" and pm.media_type != "tv":
                if stats_out is not None:
                    stats_out["media_type_skip"] = stats_out.get("media_type_skip", 0) + 1
                continue
            if rmt == "movie" and pm.media_type != "movie":
                if stats_out is not None:
                    stats_out["media_type_skip"] = stats_out.get("media_type_skip", 0) + 1
                continue

            seen_file_ids.add(bid)
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
            fid = str(f["id"])
            if fid in seen_file_ids:
                if stats_out is not None:
                    stats_out["dedup_seen"] = stats_out.get("dedup_seen", 0) + 1
                continue
            fname = f["name"]
            _, ext = os.path.splitext(strip_duplicate_suffix(fname)[0])
            if ext.lower() not in VIDEO_EXTENSIONS:
                if stats_out is not None:
                    stats_out["non_video"] = stats_out.get("non_video", 0) + 1
                continue
            pdirs = f.get("parent_dirs") or []
            pm = parse_media_context(fname, parent_dirs=pdirs, is_dir=False)
            hint = _match_hint_for_parsed(pm, pdirs, hints_by_title)
            if hint:
                pm = parse_media_context(fname, parent_dirs=pdirs, db_hint=hint, is_dir=False)

            # 规则类型精准互斥过滤：剧集规则严格跳过电影（留给电影规则），电影规则严格跳过剧集（留给剧集规则）
            if rmt == "tv" and pm.media_type != "tv":
                if stats_out is not None:
                    stats_out["media_type_skip"] = stats_out.get("media_type_skip", 0) + 1
                continue
            if rmt == "movie" and pm.media_type != "movie":
                if stats_out is not None:
                    stats_out["media_type_skip"] = stats_out.get("media_type_skip", 0) + 1
                continue

            if _should_skip_extra(fname, int(f.get("size") or 0), pm, settings):
                if stats_out is not None:
                    stats_out["extra_skip"] = stats_out.get("extra_skip", 0) + 1
                continue

            seen_file_ids.add(fid)

            siblings = f.get("sibling_entries") or []
            sub_names = [s["name"] for s in siblings if os.path.splitext(strip_duplicate_suffix(s["name"])[0])[1].lower() in SUBTITLE_EXTENSIONS]
            sub_by_name = {s["name"]: s for s in siblings}
            matched_subs = []
            if settings.get("media_move_subtitles", True) and sub_names:
                for sub_fn, lang_tag in match_subtitle_for_video(fname, sub_names):
                    s_entry = sub_by_name.get(sub_fn)
                    if s_entry:
                        seen_file_ids.add(str(s_entry["id"]))
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

    # 默认跳过已执行整理过的文件（organized_records 未撤销），避免重复预览同一批内容
    items, excluded = scan_index.exclude_organized(items)
    if stats_out is not None:
        if excluded:
            stats_out["organized_skipped"] = excluded
        if blocked:
            stats_out["blocked_rules"] = blocked
        if ctx is not None and ctx.truncated:
            stats_out["truncated"] = True
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
            raise _mw().client.Pan115Error(f"同目录存在冲突文件 {target_name}")
        _mw().client.rename_entry(file_id, target_name)
        dest_existing_names.discard(orig_name)
        dest_existing_names.add(target_name)
        return "rename"

    # 跨目录移动：若 target_name 在原目录不冲突且需改名，先改名再移动；否则先移过去再改名
    if need_rename and target_name not in src_sibling_names:
        _mw().client.rename_entry(file_id, target_name)
        src_sibling_names.discard(orig_name)
        src_sibling_names.add(target_name)
        _mw().client.move_entries([file_id], int(dest_cid))
        dest_existing_names.add(target_name)
        return "move_rename"

    _mw().client.move_entries([file_id], int(dest_cid))
    if need_rename:
        _mw().client.rename_entry(file_id, target_name)
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
        dcache[dest_root] = int(_mw().client.mkdirs(dest_root))
    dest_root_cid = dcache[dest_root]

    sub_key = (dest_root_cid, rel_dir.strip("/"))
    if sub_key not in scache:
        scache[sub_key] = int(_mw().client.ensure_subdir(dest_root_cid, rel_dir))
    dest_cid = scache[sub_key]

    if dest_cid not in ecache:
        ecache[dest_cid] = _mw().client.list_entries(dest_cid, limit=2000)
    dest_entries = ecache[dest_cid]
    dest_by_name = {e["name"]: e for e in dest_entries}
    dest_names = set(dest_by_name.keys())

    # 源父目录同级文件名（用于决定先 rename 还是先 move）
    parent_cid_int = int(str(item.get("parent_cid") or "0") or 0)
    if parent_cid_int and parent_cid_int not in ecache:
        try:
            ecache[parent_cid_int] = _mw().client.list_entries(parent_cid_int, limit=2000)
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
            trash_cid = int(_mw().client.ensure_subdir(dest_cid, "_待清理旧版"))
            _mw().client.move_entries([str(existing["id"])], trash_cid)
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
                cur_dest_entries = _mw().client.list_entries(dest_cid, limit=2000)
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
            entries = _mw().client.list_entries(int(scid), limit=50)
            if not entries:
                _mw().client.delete_entries([scid])
                removed += 1
        except Exception as e:
            logstream.warn("organize", f"清理空目录 {scid} 失败（不影响整理结果）: {e}")
    return removed
