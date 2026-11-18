"""重复文件清理执行引擎（只读扫描预览 + 确认后移入 115 回收站）。

判重口径：仅 sha1 完全相同视为重复；sha1 为空的条目一律跳过。
安全约束：
1. 每组重复文件至少保留 1 条，绝不把某一组全部删除；
2. 预览到执行重新扫描校验 file_id + sha1 + size，预览后已变动的条目跳过；
3. 与目录整理/刮削共用 `transfer_gate` 单线程互斥门禁，执行前排空转存队列；
4. 只删文件不删目录，删除即移入 115 回收站，账本单独落 `dedupe_records`。
"""
from __future__ import annotations

import asyncio
import time
import uuid

from .. import database as db
from ..core import logstream, transfer_gate
from ..pan115 import client
from . import scan_index

MAX_SCAN_DEPTH = 8
DELETE_BATCH_SIZE = 100
_SKIP_DIR_NAMES = {"@eadir", ".ds_store", "__macosx", "_待清理旧版"}
_ACTIVE_TASK_REF: dict[str, asyncio.Task | None] = {"dedupe": None}
_TASK_LOCK = asyncio.Lock()


class DedupeCancelled(Exception):
    """收到顶栏停止指令，安全中止后续删除批次。"""


def _check_not_cancelled() -> None:
    if transfer_gate.is_cancel_requested() or not is_system_running():
        raise DedupeCancelled("系统已停止运行，重复清理安全中断")


def is_system_running() -> bool:
    return str(db.get_setting("running", False)).lower() == "true"


def _walk_files_sync(root_cid: int, root_path: str, include_subdirs: bool) -> list[dict]:
    """礼貌遍历目录下全部文件条目（含 sha1），返回扁平文件清单。"""
    root = "/" + str(root_path or "").strip("/")
    queue: list[tuple[int, str, int]] = [(int(root_cid), root, 0)]
    files: list[dict] = []
    while queue:
        cid, cur_path, depth = queue.pop(0)
        entries = client.list_entries(cid, limit=5000)
        for e in entries:
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            if e.get("is_dir"):
                if not include_subdirs or depth >= MAX_SCAN_DEPTH:
                    continue
                if name.lower() in _SKIP_DIR_NAMES:
                    continue
                queue.append((int(str(e["id"])), f"{cur_path.rstrip('/')}/{name}", depth + 1))
                continue
            files.append({
                "file_id": str(e.get("id") or ""),
                "file_name": name,
                "file_path": f"{cur_path.rstrip('/')}/{name}",
                "sha1": str(e.get("sha1") or "").strip(),
                "size": int(e.get("size") or 0),
            })
        if queue:
            time.sleep(0.25 * client.pace_factor())
    return files


def _scan_rules_sync(rule_ids: list[int] | None = None, require_enabled: bool = True,
                     ctx=None):
    """同步扫描清理规则目录并按 sha1 分组，在 `run_pan_thread` 中调用。

    ctx 传入扫描上下文时走索引增量（目录 TTL 内 0 请求、整页已知提前停止、
    超预算返回部分结果）；ctx=None 时保持原有实时全量扫描（执行前复核用）。
    """
    where = ["1=1"]
    params: list = []
    if require_enabled:
        where.append("enabled=1")
    if rule_ids:
        where.append("id IN (" + ",".join("?" for _ in rule_ids) + ")")
        params.extend(int(x) for x in rule_ids)
    sql = "SELECT * FROM dedupe_rules WHERE " + " AND ".join(where) + " ORDER BY id ASC"
    rules = [dict(r) for r in db.query(sql, tuple(params))]
    if not rules:
        return rules, [], {"scanned": 0, "no_sha1": 0, "small_skipped": 0}

    stats = {"scanned": 0, "no_sha1": 0, "small_skipped": 0}
    grouped: dict[str, list[dict]] = {}
    seen_file_ids: set[str] = set()
    blocked: list[dict] = []

    for rule in rules:
        src_dir = "/" + str(rule.get("source_dir") or "").strip("/")
        ok_root, root_msg = scan_index.validate_scan_root(src_dir)
        if not ok_root:
            blocked.append({"rule_id": int(rule["id"]), "source_dir": src_dir, "msg": root_msg})
            logstream.warn("dedupe", f"清理规则「{rule.get('name') or src_dir}」{root_msg}")
            continue
        try:
            src_cid = client.resolve_dir_cid(src_dir)
            if not src_cid:
                logstream.warn("dedupe", f"清理规则「{rule.get('name') or src_dir}」源目录不存在，跳过")
                continue
            min_bytes = max(0, int(rule.get("min_size_mb") or 0)) * 1024 * 1024
            if ctx is not None:
                # 索引路径：先增量补齐索引，再用 SQL 分组（不把全盘文件攒进内存）
                subdirs = bool(int(rule.get("include_subdirs") or 0))
                _, cut = scan_index.walk_files(
                    int(src_cid), src_dir, subdirs, MAX_SCAN_DEPTH, ctx, collect=False)
                if cut:
                    ctx.truncated = True
                st = scan_index.dedupe_subtree_stats(src_dir, min_bytes, subdirs)
                for k in stats:
                    stats[k] += int(st.get(k) or 0)
                for g in scan_index.dedupe_groups(src_dir, min_bytes, subdirs):
                    fresh = [it for it in g["items"] if it["file_id"] not in seen_file_ids]
                    for it in g["items"]:
                        seen_file_ids.add(it["file_id"])
                    if len(fresh) < 2:
                        continue
                    for it in fresh:
                        it["rule_id"] = int(rule["id"])
                    grouped.setdefault(g["sha1"], []).extend(fresh)
                continue
            files = _walk_files_sync(
                int(src_cid), src_dir, bool(int(rule.get("include_subdirs") or 0)),
            )
        except Exception as e:
            logstream.warn("dedupe", f"扫描重复清理目录 {src_dir} 失败: {e}")
            continue

        for f in files:
            stats["scanned"] += 1
            fid = f["file_id"]
            if not fid or fid in seen_file_ids:
                continue
            if not f["sha1"]:
                stats["no_sha1"] += 1
                continue
            if f["size"] < min_bytes:
                stats["small_skipped"] += 1
                continue
            seen_file_ids.add(fid)
            grouped.setdefault(f["sha1"], []).append({**f, "rule_id": int(rule["id"])})

    groups: list[dict] = []
    for sha1, items in grouped.items():
        if len(items) < 2:
            continue
        groups.append({
            "sha1": sha1,
            "size": int(items[0]["size"]),
            "freeable": int(items[0]["size"]) * (len(items) - 1),
            "keep_id": items[0]["file_id"],
            "items": items,
        })
    groups.sort(key=lambda g: (g["freeable"], g["size"]), reverse=True)
    if blocked:
        stats["blocked_rules"] = blocked
    if ctx is not None and ctx.truncated:
        stats["truncated"] = True
    return rules, groups, stats


def summarize_groups(groups: list[dict]) -> dict:
    return {
        "duplicate_groups": len(groups),
        "duplicate_files": sum(len(g["items"]) - 1 for g in groups),
        "freeable_bytes": sum(int(g["freeable"] or 0) for g in groups),
    }


async def preview_dedupe(rule_ids: list[int] | None = None, ctx=None) -> dict:
    """Dry-Run 预览重复文件分组（只读，零网盘写操作）。

    ctx 由异步预览任务传入：走扫描索引增量 + 预算控制；不传则保持实时全量扫描。
    """
    if transfer_gate.is_exclusive_busy():
        return {"ok": False, "msg": "当前有独占写任务正在执行，请待结束后再预览", "groups": []}
    rules, groups, stats = await client.run_pan_thread(_scan_rules_sync, rule_ids, True, ctx)
    return {
        "ok": True,
        "rule_count": len(rules),
        "groups": groups,
        "stats": {**stats, **summarize_groups(groups)},
        "truncated": bool(stats.get("truncated")),
    }


def start_dedupe(rule_ids: int | list[int], delete_items: list[dict]) -> dict:
    """生成批次号并后台启动清理任务，立即返回 batch_id 供前端轮询。"""
    t = _ACTIVE_TASK_REF.get("dedupe")
    if t and not t.done():
        return {"ok": False, "msg": "重复清理已在执行中，请等待当前批次结束"}
    batch_id = f"ded_{uuid.uuid4().hex[:10]}"
    task = asyncio.create_task(_run_dedupe(rule_ids, delete_items, batch_id))
    _ACTIVE_TASK_REF["dedupe"] = task
    return {"ok": True, "batch_id": batch_id, "msg": "已发起重复文件清理：正在排空转存队列后移入回收站"}


def _select_valid_deletions(groups: list[dict], delete_items: list[dict]) -> tuple[list[dict], int, int]:
    """按最新扫描结果校验待删清单，返回 (可删条目, 已失效数, 整组保护数)。"""
    group_by_sha = {g["sha1"]: g for g in groups}
    requested: dict[str, set[str]] = {}
    valid: list[dict] = []
    seen_fids: set[str] = set()
    stale = 0
    for item in delete_items:
        fid = str(item.get("file_id") or "")
        if fid in seen_fids:
            continue
        sha1 = str(item.get("sha1") or "")
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        g = group_by_sha.get(sha1)
        if not g or not fid:
            stale += 1
            continue
        rec = next((x for x in g["items"] if x["file_id"] == fid and int(x["size"]) == size), None)
        if not rec:
            stale += 1
            continue
        seen_fids.add(fid)
        requested.setdefault(sha1, set()).add(fid)
        valid.append(rec)

    protected = 0
    filtered: list[dict] = []
    for rec in valid:
        g = group_by_sha[rec["sha1"]]
        ids = requested.get(rec["sha1"]) or set()
        # 整组都被勾选删除时，强制保留扫描顺序中的第一条，绝不删空整组
        if len(ids) >= len(g["items"]):
            if rec["file_id"] == g["items"][0]["file_id"]:
                protected += 1
                continue
        filtered.append(rec)
    return filtered, stale, protected


async def _run_dedupe(rule_ids: int | list[int], delete_items: list[dict], batch_id: str) -> dict:
    """执行重复清理：重新扫描校验 → 分批移入 115 回收站 → 逐条落账。"""
    if _TASK_LOCK.locked():
        return {"ok": False, "msg": "重复清理正在执行中，请勿重复发起"}
    rids = [int(rule_ids)] if isinstance(rule_ids, int) else [int(x) for x in rule_ids if int(x) > 0]
    if not rids:
        return {"ok": False, "msg": "未指定有效的清理规则"}
    ok, busy_msg = transfer_gate.try_begin_exclusive("dedupe")
    if not ok:
        logstream.warn("dedupe", busy_msg)
        return {"ok": False, "msg": busy_msg}

    deleted = 0
    failed = 0
    stale = 0
    protected = 0
    total_valid = 0
    outcome_msg = ""
    ok_flag = True
    started_at = time.monotonic()

    try:
        async with _TASK_LOCK:
            transfer_gate.clear_cancel()
            transfer_gate.set_media_progress({
                "mode": "dedupe", "phase": "draining", "current": 0, "total": 0,
                "label": "等待转存队列消化",
            })
            drained = await transfer_gate.prepare_and_drain_transfers(mode="dedupe")
            if drained is False:
                raise DedupeCancelled("排空阶段收到中断或停止信号，重复清理安全中止")
            _check_not_cancelled()

            transfer_gate.set_media_progress({
                "mode": "dedupe", "phase": "scanning", "current": 0, "total": 0,
                "label": "正在重新扫描并校验重复文件",
            })
            _, groups, _ = await client.run_pan_thread(_scan_rules_sync, rids, False)
            valid, stale, protected = _select_valid_deletions(groups, delete_items)
            total_valid = len(valid)
            if not valid:
                outcome_msg = "没有可删除的文件（预览后文件已变动或整组受保护）"
                logstream.info("dedupe", f"重复清理结束：{outcome_msg}")
            else:
                logstream.info("dedupe", f"▶ 开始执行重复清理（批次 {batch_id}，待删除 {total_valid} 项）")
                now = time.time()
                processed = 0
                per_rule_deleted: dict[int, int] = {rid: 0 for rid in rids}
                for start in range(0, total_valid, DELETE_BATCH_SIZE):
                    _check_not_cancelled()
                    chunk = valid[start:start + DELETE_BATCH_SIZE]
                    processed += len(chunk)
                    transfer_gate.set_media_progress({
                        "mode": "dedupe", "phase": "running",
                        "current": processed, "total": total_valid,
                        "label": f"正在移入回收站（第 {start // DELETE_BATCH_SIZE + 1} 批，共 {len(chunk)} 项）",
                    })
                    try:
                        await client.run_pan_thread(
                            client.delete_entries, [x["file_id"] for x in chunk])
                        deleted += len(chunk)
                        status, note = "deleted", ""
                    except Exception as e:
                        failed += len(chunk)
                        status, note = "failed", str(e)[:300]
                        logstream.warn("dedupe", f"批量移入回收站失败（{len(chunk)} 项）: {e}")
                    for rec in chunk:
                        rec_rid = int(rec.get("rule_id") or rids[0])
                        if status == "deleted":
                            per_rule_deleted[rec_rid] = per_rule_deleted.get(rec_rid, 0) + 1
                        await asyncio.to_thread(
                            db.insert,
                            "INSERT INTO dedupe_records(batch_id,rule_id,file_id,file_name,file_path,sha1,size,status,note,created_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (batch_id, rec_rid, rec["file_id"], rec["file_name"],
                             rec["file_path"], rec["sha1"], int(rec["size"]), status, note, now),
                        )
                    if processed < total_valid:
                        await asyncio.sleep(0.8 * max(1.0, client.pace_factor()))

                now = time.time()
                for rid in rids:
                    await asyncio.to_thread(
                        db.execute,
                        "UPDATE dedupe_rules SET last_run_at=?, last_deleted_count=? WHERE id=?",
                        (now, per_rule_deleted.get(rid, 0), rid),
                    )
                await asyncio.to_thread(db.set_setting, "dedupe_last_run", now)
                logstream.info(
                    "dedupe",
                    f"✅ 重复清理完成：移入回收站 {deleted}，失败 {failed}，失效跳过 {stale}，整组保护 {protected}",
                )
    except DedupeCancelled as e:
        ok_flag = False
        outcome_msg = str(e)
        logstream.warn("dedupe", f"⏸ {e}")
    except Exception as e:
        ok_flag = False
        outcome_msg = str(e)
        logstream.error("dedupe", f"重复清理异常: {logstream.exc(e)}")
    finally:
        if _ACTIVE_TASK_REF.get("dedupe") is asyncio.current_task():
            _ACTIVE_TASK_REF["dedupe"] = None
        transfer_gate.clear_cancel()
        transfer_gate.resume_all()

    elapsed = int(round(time.monotonic() - started_at))
    return {
        "ok": ok_flag,
        "batch_id": batch_id,
        "deleted": deleted,
        "failed": failed,
        "stale": stale,
        "protected": protected,
        "elapsed_sec": elapsed,
        "msg": outcome_msg,
    }
