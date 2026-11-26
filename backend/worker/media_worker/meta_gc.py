"""本地刮削元数据（.nfo / 海报）洗版淘汰与孤儿对账清理（GC）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

from ... import database as db
from ...core.tmdb_scraper import compute_local_meta_dir


def _safe_meta_path(path_str: str) -> Path | None:
    """仅允许操作本地刮削元数据根目录下的子目录，绝不越界或误删根目录本身。"""
    raw = str(path_str or "").strip()
    if not raw:
        return None
    try:
        custom = str(db.get_setting("media_local_meta_dir", "") or "").strip()
        root = compute_local_meta_dir("_probe", custom).parent.resolve()
        target = Path(raw).resolve()
        if target == root or not target.is_relative_to(root):
            return None
        return target
    except Exception:
        return None


def _remove_task_meta_files(
    task_row: dict,
    new_meta_dir: str = "",
    new_ep_stems: set[str] | None = None,
) -> tuple[int, int]:
    """清理被替换或已删除旧任务对应的单集 .nfo 与无引用旧元数据目录。"""
    nfo_removed = 0
    dirs_removed = 0
    old_dir_raw = str(task_row.get("local_meta_dir") or "").strip()
    old_dir = _safe_meta_path(old_dir_raw)
    if not old_dir or not old_dir.exists():
        return 0, 0

    new_resolved = _safe_meta_path(new_meta_dir)
    # 1. 若为剧集单集且新版文件名 stem 已变（或跨目录），删除旧单集 .nfo 并清理空季夹
    if str(task_row.get("media_type") or "") == "tv" and task_row.get("target_name"):
        old_stem = os.path.splitext(str(task_row["target_name"]))[0]
        if old_stem and (not new_ep_stems or old_stem not in new_ep_stems or (new_resolved and new_resolved != old_dir)):
            for season_dir in old_dir.glob("Season *"):
                if not season_dir.is_dir():
                    continue
                ep_nfo = season_dir / f"{old_stem}.nfo"
                if ep_nfo.is_file():
                    try:
                        ep_nfo.unlink()
                        nfo_removed += 1
                    except Exception:
                        pass
                try:
                    if not any(season_dir.iterdir()):
                        season_dir.rmdir()
                except Exception:
                    pass

    # 2. 若旧目录与新目录不同，且已无任何有效任务引用该旧目录，则整目录清理
    if not new_resolved or new_resolved != old_dir:
        ref = db.query(
            "SELECT count(*) c FROM media_tasks WHERE id!=? AND local_meta_dir=? "
            "AND status IN ('done','queued','running','staged')",
            (int(task_row["id"]), old_dir_raw),
            one=True,
        )
        if not ref or int(ref["c"] or 0) == 0:
            try:
                shutil.rmtree(old_dir, ignore_errors=True)
                dirs_removed += 1
            except Exception:
                pass

    return nfo_removed, dirs_removed


def cleanup_replaced_media_task(
    old_file_id: str,
    new_meta_dir: str = "",
    new_ep_stems: set[str] | None = None,
) -> dict:
    """洗版主动替换旧文件后，淘汰旧 file_id 的已完成任务并清理残留本地元数据。"""
    fid = str(old_file_id or "").strip()
    if not fid:
        return {"tasks_marked": 0, "nfo_removed": 0, "dirs_removed": 0}
    rows = [
        dict(r)
        for r in db.query(
            "SELECT * FROM media_tasks WHERE task_kind='scrape' AND status='done' AND file_id=?",
            (fid,),
        )
    ]
    if not rows:
        return {"tasks_marked": 0, "nfo_removed": 0, "dirs_removed": 0}

    now = time.time()
    marked = 0
    nfo_rm = 0
    dir_rm = 0
    for r in rows:
        db.execute(
            "UPDATE media_tasks SET status='replaced', updated_at=? WHERE id=?",
            (now, r["id"]),
        )
        marked += 1
        nr, dr = _remove_task_meta_files(r, new_meta_dir=new_meta_dir, new_ep_stems=new_ep_stems)
        nfo_rm += nr
        dir_rm += dr
    return {"tasks_marked": marked, "nfo_removed": nfo_rm, "dirs_removed": dir_rm}


def gc_orphan_local_metadata(live_file_ids: set[str], inplace_rule_ids: set[int]) -> dict:
    """复用全量扫描得到的存活 file_id 集合，对账清理用户手动删改后遗留的孤儿本地元数据。"""
    if not inplace_rule_ids:
        return {"tasks_marked": 0, "nfo_removed": 0, "dirs_removed": 0}
    placeholders = ",".join("?" for _ in inplace_rule_ids)
    rows = [
        dict(r)
        for r in db.query(
            f"SELECT * FROM media_tasks WHERE task_kind='scrape' AND status='done' AND rule_id IN ({placeholders})",
            tuple(sorted(inplace_rule_ids)),
        )
    ]
    now = time.time()
    marked = 0
    nfo_rm = 0
    dir_rm = 0
    for r in rows:
        fid = str(r.get("file_id") or "").strip()
        if not fid or fid in live_file_ids:
            continue
        # 合并剧集组中只要仍有任意一集存活，就不淘汰整组任务
        try:
            pj = json.loads(r.get("parsed_json") or "{}")
            grp = pj.get("_grouped_items") or []
            if any(str(gi.get("file_id") or "") in live_file_ids for gi in grp):
                continue
        except Exception:
            pass
        db.execute(
            "UPDATE media_tasks SET status='replaced', updated_at=? WHERE id=?",
            (now, r["id"]),
        )
        marked += 1
        nr, dr = _remove_task_meta_files(r)
        nfo_rm += nr
        dir_rm += dr
    return {"tasks_marked": marked, "nfo_removed": nfo_rm, "dirs_removed": dir_rm}
