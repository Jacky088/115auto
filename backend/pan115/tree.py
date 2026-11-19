"""115 目录树遍历（walk_media_tree，支持 BDMV 原盘保护）与剧集分季整理（organize_seasons）。"""
from __future__ import annotations

import re
import time


def _pan():
    """延迟获取 client 模块引用，避免循环导入且保证测试 patch 透明生效。"""
    from . import client
    return client


_EP_SEASON_RE = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee]\d{1,3}")
_EP_CN_RE = re.compile(r"第\s?(\d{1,3})\s?[集话話]")


def organize_seasons(cid: int, min_files: int = 2) -> int:
    """把目录下的剧集文件按季归入「Season N」子目录，返回移动的文件数。

    - 只处理带集数标识（S01E01 / 第1集）的文件；第N集默认按第 1 季
    - 单季少于 min_files 个文件时不建目录（避免为两三集碎片建一堆空壳）
    - 目录已存在时复用
    """
    pan = _pan()
    client = pan._get_client()
    entries = pan.list_entries(cid)
    groups: dict[int, list[dict]] = {}
    for e in entries:
        if e["is_dir"]:
            continue
        m = _EP_SEASON_RE.search(e["name"]) or _EP_CN_RE.search(e["name"])
        if not m:
            continue
        season = 1 if _EP_CN_RE.fullmatch(m.group(0).strip()) else max(int(m.group(1)), 1)
        groups.setdefault(season, []).append(e)
    moved = 0
    for season, items in sorted(groups.items()):
        if len(items) < min_files:
            continue
        sname = f"Season {season}"
        target = next((e for e in entries if e["is_dir"] and e["name"] == sname), None)
        if target is None:
            resp = pan.pan_call(client.fs_mkdir, {"cname": sname, "pid": cid})
            if not resp.get("state"):
                raise pan.Pan115Error(f"创建 {sname} 失败: {str(resp)[:200]}")
            data = resp.get("data", {}) or {}
            new_id = pan._to_int(data.get("file_id") or data.get("cid") or resp.get("cid"), 0)
            if not new_id:
                new_id = pan._list_dir(cid).get(sname, 0)
            target = {"id": str(new_id), "name": sname, "is_dir": True}
            entries.append(target)
            time.sleep(0.5)
        # 用 move_entries（分批 + 校验响应 + 失效缓存）替代裸 fs_move，
        # 避免移动失败被静默忽略或单批数量超限
        pan.move_entries([str(i["id"]) for i in items], int(str(target["id"])))
        moved += len(items)
        time.sleep(0.5)
    return moved


def walk_media_tree(root_cid: int, root_path: str, max_depth: int = 4) -> dict:
    """礼貌遍历 115 指定目录下的文件树（自动将 BDMV 原盘视为整体目录不拆散内部流文件）。

    返回 {"files": [...], "bdmv_dirs": [...], "dirs": [...]}
    """
    pan = _pan()
    norm_root = "/" + root_path.strip("/")
    queue_dirs: list[tuple[int, str, list[str], int]] = [(int(root_cid), norm_root, [], 0)]
    files_out: list[dict] = []
    bdmv_out: list[dict] = []
    dirs_out: list[dict] = []

    while queue_dirs:
        cur_cid, cur_path, rel_parents, depth = queue_dirs.pop(0)
        if depth > max_depth:
            continue
        entries = pan.list_entries(cur_cid, limit=2000)
        dir_names_lower = {e["name"].lower() for e in entries if e.get("is_dir")}
        # BDMV / VIDEO_TS 原盘保护：父目录视作原子整体，不再深入枚举 Stream/*.m2ts
        if depth > 0 and ("bdmv" in dir_names_lower or "certificate" in dir_names_lower or "video_ts" in dir_names_lower):
            bdmv_out.append({
                "id": str(cur_cid),
                "name": rel_parents[-1],
                "path": cur_path,
                "parent_dirs": rel_parents[:-1],
                "parent_cid": "",  # 由外层记录填充
                "is_bdmv": True,
            })
            continue

        sibling_names = [e["name"] for e in entries if not e.get("is_dir")]
        # 目录内文件条目只在目录级构建一次，同目录所有文件共享同一列表引用——
        # 旧写法为每个文件各复制一份过滤列表，大目录下 O(N²) 内存放大
        sibling_file_entries = [x for x in entries if not x.get("is_dir")]
        for e in entries:
            if e.get("is_dir"):
                if e["name"].lower() in ("@eadir", ".ds_store", "__macosx", "_待清理旧版"):
                    continue
                sub_path = f"{cur_path.rstrip('/')}/{e['name']}"
                dirs_out.append({
                    "id": str(e["id"]),
                    "name": e["name"],
                    "path": sub_path,
                    "parent_cid": str(cur_cid),
                    "depth": depth + 1,
                })
                if depth + 1 <= max_depth:
                    queue_dirs.append((int(str(e["id"])), sub_path, rel_parents + [e["name"]], depth + 1))
            else:
                files_out.append({
                    "id": str(e["id"]),
                    "name": e["name"],
                    "size": int(e.get("size") or 0),
                    "sha1": e.get("sha1") or "",
                    "parent_cid": str(cur_cid),
                    "dir_path": cur_path,
                    "parent_dirs": list(rel_parents),
                    "sibling_names": sibling_names,
                    "sibling_entries": sibling_file_entries,
                })
        if queue_dirs:
            time.sleep(0.25 * pan.pace_factor())

    # 补全 bdmv_dirs 的 parent_cid
    dir_parent_map = {d["path"]: d["parent_cid"] for d in dirs_out}
    for b in bdmv_out:
        b["parent_cid"] = dir_parent_map.get(b["path"], "")

    return {"files": files_out, "bdmv_dirs": bdmv_out, "dirs": dirs_out}
