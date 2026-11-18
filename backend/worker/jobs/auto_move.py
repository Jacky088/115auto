"""定时文件整理调度、只读预览、独占串行执行、台账撤销与后台循环。"""
import asyncio
import datetime
import json
import re
import time
import uuid

from ... import database as db
from ...core import logstream, transfer_gate
from ...pan115 import client as pan
from .. import scan_index

def _jobs():
    """延迟获取 jobs 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import jobs
    return jobs


_auto_move_lock: asyncio.Lock | None = None
_last_auto_move_result: dict = {}


def _get_auto_move_lock() -> asyncio.Lock:
    global _auto_move_lock
    if _auto_move_lock is None:
        _auto_move_lock = asyncio.Lock()
    return _auto_move_lock


def get_last_auto_move_result() -> dict:
    return _last_auto_move_result


def compute_next_runs() -> list[str]:
    """计算未来 48 小时（2 天）内预计的移动整理触发时间点列表。"""
    sched_type = str(db.get_setting("auto_move_schedule_type", "interval") or "interval")
    now = time.time()
    next_times = []

    if sched_type == "fixed_times":
        fixed_times_raw = db.get_setting("auto_move_fixed_times", '["06:00", "12:00", "18:00"]')
        try:
            if isinstance(fixed_times_raw, str):
                fixed_times = json.loads(fixed_times_raw)
            elif isinstance(fixed_times_raw, list):
                fixed_times = fixed_times_raw
            else:
                fixed_times = []
        except Exception:
            fixed_times = []

        valid_times = []
        for item in fixed_times:
            m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", str(item).strip())
            if m:
                valid_times.append(f"{int(m.group(1)):02d}:{int(m.group(2)):02d}")
        valid_times = sorted(set(valid_times))
        if not valid_times:
            return []

        # 遍历今天、明天、后天、大后天（覆盖未来 48 小时）
        today = datetime.date.today()
        for day_offset in range(4):
            d = today + datetime.timedelta(days=day_offset)
            d_str = d.strftime("%Y-%m-%d")
            for t_str in valid_times:
                dt_str = f"{d_str} {t_str}"
                try:
                    t_struct = time.strptime(dt_str, "%Y-%m-%d %H:%M")
                    ts = time.mktime(t_struct)
                    if now < ts <= now + 48 * 3600:
                        next_times.append(ts)
                except Exception:
                    pass
    else:
        interval_hours = max(float(db.get_setting("auto_move_interval_hours", 6) or 6), 1.0)
        interval_sec = interval_hours * 3600
        last_run = float(db.get_setting("auto_move_last_run", 0) or 0)

        if last_run <= 0:
            base_t = now
        else:
            base_t = last_run
            while base_t + interval_sec <= now:
                base_t += interval_sec

        cur_t = base_t + interval_sec if base_t <= now else base_t
        while cur_t <= now + 48 * 3600:
            next_times.append(cur_t)
            cur_t += interval_sec

    next_times.sort()

    results = []
    today_str = time.strftime("%Y-%m-%d")
    tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    day_after_str = (datetime.date.today() + datetime.timedelta(days=2)).strftime("%Y-%m-%d")

    for ts in next_times:
        d_part = time.strftime("%Y-%m-%d", time.localtime(ts))
        hm_part = time.strftime("%H:%M", time.localtime(ts))
        if d_part == today_str:
            prefix = "今天"
        elif d_part == tomorrow_str:
            prefix = "明天"
        elif d_part == day_after_str:
            prefix = "后天"
        else:
            prefix = time.strftime("%m月%d日", time.localtime(ts))
        results.append(f"{prefix} {hm_part}")
    return results


async def execute_auto_move(trigger: str = "scheduled", include_ids: list[str] | None = None) -> dict:
    """按 auto_move_rules 顺序排队执行文件移动整理（做好 115 严格防风控与通知推送）。

    include_ids 非 None 时，仅移动预览阶段用户明确勾选的 file_id（预览后新增文件不处理）。
    """
    global _last_auto_move_result

    def _preflight_cfg():
        return (
            str(db.get_setting("running", False)).lower() == "true",
            bool(db.get_setting("auto_move_enabled", False)),
        )

    is_running, am_enabled = await asyncio.to_thread(_preflight_cfg)
    if not is_running:
        msg = "系统处于停止状态，自动整理取消执行"
        logstream.warn("auto_move", msg)
        return {"ok": False, "msg": msg}
    if not am_enabled:
        msg = "定时文件整理功能未开启，请先打开功能总开关后再执行！"
        logstream.warn("auto_move", msg)
        return {"ok": False, "msg": msg}
    if trigger == "scheduled" and not await asyncio.to_thread(transfer_gate.is_in_schedule_window):
        msg = "当前处于防风控静默时段外，定时文件整理暂停执行"
        logstream.warn("auto_move", msg)
        return {"ok": False, "msg": msg}

    lock = _get_auto_move_lock()
    if lock.locked():
        return {"ok": False, "msg": "整理任务正在执行中，请勿重复发起"}

    async with lock:
        rules = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM auto_move_rules WHERE enabled=1 ORDER BY id ASC")]
        if not rules:
            res = {"ok": True, "total_rules": 0, "total_moved": 0, "details": []}
            _last_auto_move_result = res
            return res

        # 整理准备：获取四方独占门禁 → 排空现有转存队列（包括云下载与重试）直到全部完成终态，再开启移动
        ok_gate, busy_msg = transfer_gate.try_begin_exclusive("auto_move")
        if not ok_gate:
            return {"ok": False, "msg": busy_msg}
        try:
            drained = await transfer_gate.prepare_and_drain_transfers(mode="auto_move")
            if drained is False or transfer_gate.is_cancel_requested():
                return {"ok": False, "cancelled": True, "msg": "排队等待期间已取消"}
            details = []
            # 整理账本批次号：一次执行 = 一个批次，撤销与追溯都以它为键
            batch_id = uuid.uuid4().hex[:12]

            # 账本保留策略：90 天前的整理记录自动清理（低频数据，随执行顺手清理）
            await asyncio.to_thread(
                db.execute,
                "DELETE FROM organized_records WHERE action='auto_move' AND created_at < ?",
                (time.time() - 90 * 86400,))

            def _load_move_opts():
                return (
                    bool(db.get_setting("auto_move_scan_subdirs", True)),
                    db.get_setting("auto_move_mode", "library"),
                    bool(db.get_setting("auto_move_season_folder", True)),
                    bool(db.get_setting("auto_move_fuzzy_t2s", True)),
                    bool(db.get_setting("auto_move_dedup_by_episode", True)),
                )

            scan_subdirs, global_mode, season_folder, fuzzy_t2s, dedup_by_episode = await asyncio.to_thread(_load_move_opts)
            wanted_ids = {str(x) for x in include_ids} if include_ids is not None else None
            risk_aborted = False
            scan_cache: dict = {}

            # 跨规则同名歧义预检：≥2 个总库规则时，收集被多库共有的剧名关键词。
            # 复用 scan_cache——预检列过的库根目录，移动阶段零重复请求
            lib_targets = []
            for r in rules:
                rm = r.get("mode") or "default"
                em = rm if rm in ("library", "exact") else global_mode
                if em == "library" and r.get("target_dir", "").strip():
                    lib_targets.append(r["target_dir"].strip())
            lib_targets = list(dict.fromkeys(lib_targets))  # 同一总库被多条规则指向只算一个
            ambiguous_kws: set[str] = set()
            if len(lib_targets) >= 2:
                ambiguous_kws = await pan.run_pan_thread(
                    pan.collect_library_keyword_conflicts, lib_targets, fuzzy_t2s, scan_cache)
                if ambiguous_kws:
                    logstream.info(
                        "auto_move",
                        f"同名歧义预检：{len(lib_targets)} 个总库存在同名剧名 "
                        f"{sorted(ambiguous_kws)[:5]}，无集数标识的裸文件名将留源目录交用户手动处理")

            # 云下载目录模式运行时兜底：配置漂移（路由里的云下载目录后来改了）则跳过并告警
            cd_dirs = await asyncio.to_thread(cloud_download_dirs)

            for r in rules:
                s_dir = r.get("source_dir", "").strip()
                t_dir = r.get("target_dir", "").strip()
                rule_mode = r.get("mode") or "default"
                effective_mode = rule_mode if rule_mode in ("library", "exact") else global_mode
                kw = (r.get("keyword", "") or pan.extract_target_folder_name(t_dir)).strip()

                if effective_mode == "exact" and not kw:
                    msg = f"规则 #{r['id']} 目标目录「{t_dir}」未能提取有效文件夹名称，跳过"
                    logstream.warn("auto_move", msg)
                    details.append({"id": r["id"], "source_dir": s_dir, "target_dir": t_dir,
                                    "keyword": "", "mode": effective_mode, "moved": 0, "error": "目标文件夹名为空，跳过执行"})
                    continue

                # 按父目录名匹配（云下载模式）的运行时校验：源目录必须仍是配置的云下载目录
                if r.get("dir_match") and s_dir not in cd_dirs:
                    msg = (f"规则 #{r['id']} 源目录 {s_dir} 不是当前配置的云下载目录"
                           f"（{'、'.join(cd_dirs) or '未配置'}），云下载模式未生效，本轮跳过。"
                           "请检查规则配置或更新分类路由的云下载目录")
                    logstream.warn("auto_move", msg)
                    details.append({"id": r["id"], "source_dir": s_dir, "target_dir": t_dir,
                                    "keyword": kw, "mode": effective_mode, "moved": 0,
                                    "error": "源目录不是云下载目录，云下载模式未生效"})
                    continue

                try:
                    moved_items: list = []
                    moved_skip: dict = {}
                    moved_count, sample_names = await pan.run_pan_thread(
                        pan.move_matched_files_for_rule,
                        s_dir, t_dir, kw,
                        scan_subdirs=scan_subdirs,
                        mode=effective_mode,
                        season_folder=season_folder,
                        fuzzy_t2s=fuzzy_t2s,
                        dedup_by_episode=dedup_by_episode,
                        max_depth=2,
                        scan_cache=scan_cache,
                        moved_out=moved_items,
                        rule_media_type=str(r.get("media_type") or "auto"),
                        ambiguous_kws=ambiguous_kws,
                        match_by_dir=bool(r.get("dir_match")),
                        skip_stats_out=moved_skip,
                        include_ids=wanted_ids,
                    )
                    await asyncio.to_thread(
                        db.execute,
                        "UPDATE auto_move_rules SET last_run_at=?, last_moved_count=? WHERE id=?",
                        (time.time(), moved_count, r["id"]),
                    )
                    # 整理账本：成功移动的文件逐条落记录（撤销与「整理记录」页的数据源）。
                    # 只在移动成功后落账；账本写入失败不回滚移动，只记警告（移动已实际发生）
                    if moved_items:
                        def _write_ledger(items=moved_items, rid=r["id"], bid=batch_id):
                            now = time.time()
                            with db.transaction() as con:
                                for it in items:
                                    con.execute(
                                        "INSERT INTO organized_records(task_id,file_id,parent_cid,size,"
                                        "orig_path,orig_name,dest_cid,dest_path,dest_name,action,"
                                        "batch_id,undone,undo_note,created_at) "
                                        "VALUES(?,?,?,?,?,?,?,?,?,'auto_move',?,0,'',?)",
                                        (int(rid), str(it["id"]), str(it.get("from_cid") or ""),
                                         int(it.get("size") or 0), str(it.get("from_path") or ""),
                                         str(it["name"]), str(it.get("dest_cid") or ""),
                                         str(it.get("dest_path") or ""), str(it["name"]),
                                         bid, now))
                        try:
                            await asyncio.to_thread(_write_ledger)
                        except Exception as le:
                            logstream.warn("auto_move", f"整理账本写入失败（移动已生效）: {le}")
                    mode_label = "总库分流" if effective_mode == "library" else f"精确匹配({kw})"
                    details.append({
                        "id": r["id"], "source_dir": s_dir, "target_dir": t_dir,
                        "keyword": kw, "mode": effective_mode, "moved": moved_count, "sample_names": sample_names,
                        "skips": moved_skip,
                    })
                    if moved_count > 0:
                        logstream.info("auto_move",
                                       f"整理完成 [{s_dir} ➔ {t_dir} / {mode_label}]：成功移动 {moved_count} 个文件")
                    else:
                        logstream.info("auto_move",
                                       f"整理扫描 [{s_dir} ➔ {t_dir} / {mode_label}]：无新文件需要移动")
                except Exception as e:
                    err_str = str(e)
                    logstream.warn("auto_move", f"规则 #{r['id']} [{s_dir} ➔ {t_dir}] 执行异常: {err_str}")
                    details.append({"id": r["id"], "source_dir": s_dir, "target_dir": t_dir,
                                    "keyword": kw, "moved": 0, "error": err_str})
                    if any(k in err_str.lower() for k in pan._RISK_KEYWORDS):
                        risk_aborted = True
                        logstream.error("auto_move", f"⚠️ 检测到 115 风控限制信号，自动熔断停止后续移动操作: {err_str}")
                        await pan.run_pan_thread(
                            _jobs().notify,
                            f"⚠️ 【115 风控拦截】自动整理文件时检测到 115 限制信号（{err_str[:100]}），已紧急熔断停止后续移动操作，请检查账号状态！"
                        )
                        break

                # 规则与规则之间的排队间隔，自适应风控防封号
                pace = pan.pace_factor()
                await asyncio.sleep(1.5 * pace)

            total_moved = sum(d.get("moved", 0) for d in details)
            await asyncio.to_thread(db.set_setting, "auto_move_last_run", time.time())

            # 移动完成推送通知。触发门槛：有移动/手动执行/风控熔断，或出现同名歧义——
            # 歧义=需要用户手动判断的信号（两库同名且无集数标识），必须提醒
            amb_total = sum(int((d.get("skips") or {}).get("ambiguous") or 0) for d in details)
            if await asyncio.to_thread(db.get_setting, "auto_move_notify", True) and (
                    total_moved > 0 or trigger == "manual" or risk_aborted or amb_total > 0):
                trig_desc = "手动排队执行" if trigger == "manual" else "定时计划执行"
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                lines = [
                    "📁 115 文件整理任务完成",
                    f"• 触发方式：{trig_desc}",
                    f"• 完成时间：{now_str}",
                    f"• 规则统计：共处理 {len(rules)} 条规则，成功移动 {total_moved} 个文件",
                ]
                if amb_total > 0:
                    lines.append(f"⚠️ 有 {amb_total} 个文件因两库同名且无集数标识未自动处理"
                                 "（留在源目录），请手动判断归档")
                if risk_aborted:
                    lines.append("⚠️ 注意：任务因触发 115 接口风控而提前中断，部分规则未执行")
                lines.append("")
                lines.append("明细：")
                for d in details:
                    sk = d.get("skips") or {}
                    skip_parts = []
                    if sk.get("ep"):
                        skip_parts.append(f"按季集防重跳过 {sk['ep']}")
                    if sk.get("ambiguous"):
                        skip_parts.append(f"同名歧义跳过 {sk['ambiguous']}")
                    if sk.get("movie"):
                        skip_parts.append(f"电影库守卫跳过 {sk['movie']}")
                    if sk.get("junk"):
                        skip_parts.append(f"垃圾过滤 {sk['junk']}")
                    if sk.get("dedup"):
                        skip_parts.append(f"同名同内容查重 {sk['dedup']}")
                    skip_txt = ("；" + "、".join(skip_parts)) if skip_parts else ""
                    if d.get("error"):
                        lines.append(f"• {d['target_dir']}：❌ 执行失败（{d['error'][:50]}）")
                    elif d.get("moved", 0) > 0:
                        samples = "、".join(d.get("sample_names", [])[:2])
                        extra = f"（如 {samples} 等）" if samples else ""
                        lines.append(f"• {d['target_dir']}（关键词: {d['keyword']}）："
                                     f"移动 {d['moved']} 个文件{extra}{skip_txt}")
                    elif skip_parts:
                        lines.append(f"• {d['target_dir']}（关键词: {d['keyword']}）："
                                     f"无新移动（{'、'.join(skip_parts)}）")
                    else:
                        lines.append(f"• {d['target_dir']}（关键词: {d['keyword']}）：无新文件")
                lines.append("")
                # 提示行按规则形态区分：云下载模式按任务目录名匹配，与普通规则口径不同
                if any(r.get("dir_match") for r in rules):
                    lines.append("💡 提示：文件夹均保持原地；普通规则按文件名匹配，"
                                 "云下载模式规则按任务目录名匹配（英文名文件认领后保持原名）。")
                else:
                    lines.append("💡 提示：文件夹均保持原地，仅移动含中文且含目标名称的正片文件。")
                full_msg = chr(10).join(lines)
                await pan.run_pan_thread(_jobs().notify, full_msg)

            res = {"ok": True, "total_rules": len(rules), "total_moved": total_moved, "details": details}
            _last_auto_move_result = res
            return res
        finally:
            # 整理完成或异常中止，停止移动任务并恢复新任务接收与转存工作器
            transfer_gate.resume_all()


def cloud_download_dirs() -> list[str]:
    """当前配置的云下载目录集合（归一化为 /开头）。

    权威来源=分类路由：每个分类取 offline_dir，未设置时回落该分类的 target_dir；
    空值与 'skip' 排除。供「按父目录名匹配」规则的保存校验与运行时兜底校验使用。
    """
    dirs = set()
    for r in db.query("SELECT target_dir, offline_dir FROM routes"):
        od = str(r["offline_dir"] or "").strip()
        if not od or od.lower() == "skip":
            od = str(r["target_dir"] or "").strip()
        if not od or od.lower() == "skip":
            continue
        dirs.add("/" + od.strip("/"))
    return sorted(dirs)


async def preview_auto_move(rule_ids: list[int] | None = None, ctx=None) -> dict:
    """整理前预览（dry_run）：按启用规则扫描源目录，返回「将会移动哪些文件」的计划。

    只读不写：不做任何移动/重命名；不进独占门闸（不需要暂停转存工作器），
    但独占任务执行中拒绝预览（避免叠加 115 扫描请求），并与正式执行共用模块锁互斥。
    预览的 115 请求量与正式执行相当（只少 fs_move），结果为当前时刻快照，
    确认执行时会重新扫描（期间新增/手动移走的文件与预览存在差异）。
    ctx 由异步预览任务传入：按规则推进进度，并在预算耗尽时返回部分结果。
    """
    def _preflight():
        return (str(db.get_setting("running", False)).lower() == "true",
                bool(db.get_setting("auto_move_enabled", False)))

    is_running, am_enabled = await asyncio.to_thread(_preflight)
    if not is_running:
        return {"ok": False, "msg": "系统处于停止状态，请先在顶栏点击「运行」后再预览"}
    if not am_enabled:
        return {"ok": False, "msg": "定时文件整理功能未开启，请先打开功能总开关"}
    if transfer_gate.is_exclusive_busy():
        return {"ok": False, "msg": f"当前有互斥任务（{transfer_gate.get_active_mode()}）执行中，请待结束后再预览"}

    lock = _get_auto_move_lock()
    if lock.locked():
        return {"ok": False, "msg": "整理任务正在执行中，请稍后再预览"}

    async with lock:
        rules = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM auto_move_rules WHERE enabled=1 ORDER BY id ASC")]
        if rule_ids:
            want = {int(x) for x in rule_ids if str(x).isdigit()}
            rules = [r for r in rules if r["id"] in want]
        if not rules:
            return {"ok": True, "total_planned": 0, "rules": []}

        def _load_move_opts():
            return (
                bool(db.get_setting("auto_move_scan_subdirs", True)),
                db.get_setting("auto_move_mode", "library"),
                bool(db.get_setting("auto_move_season_folder", True)),
                bool(db.get_setting("auto_move_fuzzy_t2s", True)),
                bool(db.get_setting("auto_move_dedup_by_episode", True)),
            )

        scan_subdirs, global_mode, season_folder, fuzzy_t2s, dedup_by_episode = \
            await asyncio.to_thread(_load_move_opts)
        scan_cache: dict = {}

        # 跨规则同名歧义预检（与正式执行同口径，预览如实反映将跳过的歧义文件）
        lib_targets = []
        for r in rules:
            rm = r.get("mode") or "default"
            em = rm if rm in ("library", "exact") else global_mode
            if em == "library" and r.get("target_dir", "").strip():
                lib_targets.append(r["target_dir"].strip())
        lib_targets = list(dict.fromkeys(lib_targets))  # 同一总库被多条规则指向只算一个
        ambiguous_kws: set[str] = set()
        if len(lib_targets) >= 2:
            ambiguous_kws = await pan.run_pan_thread(
                pan.collect_library_keyword_conflicts, lib_targets, fuzzy_t2s, scan_cache)
        cd_dirs = await asyncio.to_thread(cloud_download_dirs)

        out_rules: list[dict] = []
        total_planned = 0
        risk_aborted = False
        truncated = False

        for r in rules:
            if ctx is not None:
                if not ctx.can_continue() or not ctx.begin_dir():
                    truncated = True
                    break
            s_dir = r.get("source_dir", "").strip()
            t_dir = r.get("target_dir", "").strip()
            rule_mode = r.get("mode") or "default"
            effective_mode = rule_mode if rule_mode in ("library", "exact") else global_mode
            kw = (r.get("keyword", "") or pan.extract_target_folder_name(t_dir)).strip()
            entry = {"rule_id": r["id"], "source_dir": s_dir, "target_dir": t_dir,
                     "mode": effective_mode, "keyword": kw,
                     "planned": [], "skip_ep": 0, "skip_ep_samples": [],
                     "skip_movie": 0, "skip_ambiguous": 0, "skip_ambiguous_samples": [], "error": ""}

            ok_root, root_msg = scan_index.validate_scan_root(s_dir)
            if not ok_root:
                entry["error"] = root_msg
                out_rules.append(entry)
                continue
            if ctx is not None:
                ctx.set_phase("scanning", f"正在扫描 {s_dir} → {t_dir}")
            if effective_mode == "exact" and not kw:
                entry["error"] = "目标文件夹名为空，无法提取匹配关键词"
                out_rules.append(entry)
                continue
            if r.get("dir_match") and s_dir not in cd_dirs:
                entry["error"] = (f"源目录 {s_dir} 不是当前配置的云下载目录"
                                  f"（{'、'.join(cd_dirs) or '未配置'}），云下载模式未生效")
                out_rules.append(entry)
                continue
            try:
                planned_cnt, plan = await pan.run_pan_thread(
                    pan.move_matched_files_for_rule,
                    s_dir, t_dir, kw,
                    scan_subdirs=scan_subdirs,
                    mode=effective_mode,
                    season_folder=season_folder,
                    fuzzy_t2s=fuzzy_t2s,
                    dedup_by_episode=dedup_by_episode,
                    max_depth=2,
                    scan_cache=scan_cache,
                    dry_run=True,
                    rule_media_type=str(r.get("media_type") or "auto"),
                    ambiguous_kws=ambiguous_kws,
                    match_by_dir=bool(r.get("dir_match")),
                )
                if isinstance(plan, dict):
                    entry["planned"] = plan.get("items", [])
                    entry["skip_ep"] = int(plan.get("skip_ep") or 0)
                    entry["skip_ep_samples"] = list(plan.get("skip_ep_samples") or [])
                    entry["skip_movie"] = int(plan.get("skip_movie") or 0)
                    entry["skip_ambiguous"] = int(plan.get("skip_ambiguous") or 0)
                    entry["skip_ambiguous_samples"] = list(plan.get("skip_ambiguous_samples") or [])
                    entry["skip_junk"] = int(plan.get("skip_junk") or 0)
                    entry["skip_dedup"] = int(plan.get("skip_dedup") or 0)
                total_planned += len(entry["planned"])
                log_parts = [f"计划移动 {len(entry['planned'])} 个文件"]
                if entry["skip_ep"]:
                    log_parts.append(f"按季集防重将跳过 {entry['skip_ep']} 个")
                if entry["skip_movie"]:
                    log_parts.append(f"电影库守卫跳过 {entry['skip_movie']} 个")
                if entry["skip_ambiguous"]:
                    log_parts.append(f"同名歧义跳过 {entry['skip_ambiguous']} 个"
                                     f"（{'、'.join(entry['skip_ambiguous_samples'][:2])}）")
                if entry["skip_junk"]:
                    log_parts.append(f"垃圾过滤跳过 {entry['skip_junk']} 个")
                if entry["skip_dedup"]:
                    log_parts.append(f"同名/同内容查重跳过 {entry['skip_dedup']} 个")
                logstream.info("auto_move", f"预览 [{s_dir} ➔ {t_dir}]：" + "，".join(log_parts))
            except Exception as e:
                entry["error"] = str(e)[:200]
                logstream.warn("auto_move", f"预览规则 #{r['id']} [{s_dir} ➔ {t_dir}] 异常: {e}")
                if any(k in str(e).lower() for k in pan._RISK_KEYWORDS):
                    risk_aborted = True
                    out_rules.append(entry)
                    break
            out_rules.append(entry)
            # 规则之间小间隔，与正式执行的防风控口径一致（预览不触发写操作，间隔可短些）
            await asyncio.sleep(0.3)

        if risk_aborted:
            logstream.error("auto_move", "⚠️ 预览过程触发 115 风控信号，已中止剩余规则的预览")
        if truncated and ctx is not None:
            ctx.truncated = True
        return {"ok": True, "total_planned": total_planned, "rules": out_rules,
                "risk_aborted": risk_aborted, "truncated": truncated}


def _undo_auto_move_sync(records: list[dict]) -> dict:
    """同步撤销整理账本记录：把文件从账本目标目录移回源目录（经 pan_call 串行节流）。

    位置漂移防护（绝不盲移）：
    - 文件仍在账本目标目录 → 移回记录的源目录（源目录被删则按 orig_path 逐级重建）
    - 文件已在源目录（用户自己移回来了）→ 不动文件，直接标记已撤销
    - 两处都不在（被手动移动/删除/二次整理）→ 跳过并记 undo_note，请用户人工处理
    """
    undone = 0
    failed = 0
    skipped: list[dict] = []

    def _dir_ids(cid: int) -> set[str] | None:
        try:
            return {str(e["id"]) for e in pan.list_entries(cid, limit=2000)}
        except Exception:
            return None

    dest_cache: dict[int, set[str] | None] = {}
    src_cache: dict[int, set[str] | None] = {}

    for rec in records:
        fid = str(rec["file_id"] or "")
        name = str(rec["orig_name"] or "")
        try:
            dest_cid = int(str(rec.get("dest_cid") or 0) or 0)
            from_cid = int(str(rec.get("parent_cid") or 0) or 0)
            orig_path = str(rec.get("orig_path") or "")
            if not fid or dest_cid <= 0:
                raise RuntimeError("账本记录缺少文件 id 或目标目录")

            if dest_cid not in dest_cache:
                dest_cache[dest_cid] = _dir_ids(dest_cid)
            in_dest = dest_cache[dest_cid] is not None and fid in dest_cache[dest_cid]

            if not in_dest:
                # 不在目标目录：看是否已被移回源目录（用户手动移回的场景）
                if from_cid > 0:
                    if from_cid not in src_cache:
                        src_cache[from_cid] = _dir_ids(from_cid)
                    if src_cache[from_cid] is not None and fid in src_cache[from_cid]:
                        db.execute("UPDATE organized_records SET undone=1, undo_note=? WHERE id=?",
                                   ("文件已在原位置", rec["id"]))
                        undone += 1
                        continue
                db.execute("UPDATE organized_records SET undo_note=? WHERE id=? AND undone=0",
                           ("位置已变化（文件已不在记录的目标目录），请人工核对", rec["id"]))
                skipped.append({"name": name, "reason": "位置已变化，请人工核对"})
                continue

            # 源目录兜底：记录的源 cid 无效时按 orig_path（源目录完整路径）逐级重建
            target_from_cid = from_cid
            if target_from_cid <= 0 or target_from_cid == dest_cid:
                target_from_cid = pan.mkdirs(orig_path) if orig_path else 0
            if target_from_cid <= 0 or target_from_cid == dest_cid:
                db.execute("UPDATE organized_records SET undo_note=? WHERE id=? AND undone=0",
                           ("源目录不可用，无法移回，请人工核对", rec["id"]))
                skipped.append({"name": name, "reason": "源目录不可用"})
                continue

            try:
                pan.move_entries([fid], target_from_cid)
            except Exception:
                # 源目录可能已被清理（移动失败常因父目录不存在）：按路径重建后重试一次
                if not orig_path:
                    raise
                rebuilt = pan.mkdirs(orig_path)
                if not rebuilt or rebuilt == dest_cid:
                    raise
                pan.move_entries([fid], rebuilt)
                target_from_cid = rebuilt
            db.execute("UPDATE organized_records SET undone=1, undo_note='' WHERE id=?", (rec["id"],))
            undone += 1
            if dest_cache.get(dest_cid) is not None:
                dest_cache[dest_cid].discard(fid)
            time.sleep(0.6 * pan.pace_factor())
        except Exception as e:
            failed += 1
            logstream.warn("auto_move", f"撤销记录 #{rec['id']}({name}) 失败: {e}")
    return {"undone": undone, "failed": failed, "skipped": skipped}


async def undo_auto_move_records(record_id: int = 0, batch_id: str = "",
                                 stop_rule_ids: list[int] | None = None) -> dict:
    """撤销整理记录（单条或整批），可选同时停用涉及的规则。

    撤销后文件回到源目录——若规则仍启用且文件仍命中匹配条件，下一轮定时整理会
    再次把它移走；前端据此提供「撤销并停用规则」选项（stop_rule_ids），停用后
    启用规则归零时联动关闭功能总开关（与删除规则的行为一致）。
    """
    if transfer_gate.is_exclusive_busy():
        return {"ok": False, "msg": f"当前有互斥任务（{transfer_gate.get_active_mode()}）执行中，请待结束后再撤销"}
    if not record_id and not batch_id:
        return {"ok": False, "msg": "请指定要撤销的记录或批次"}

    if record_id:
        rows = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM organized_records WHERE id=? AND action='auto_move' AND undone=0",
            (int(record_id),))]
    else:
        rows = [dict(r) for r in await asyncio.to_thread(
            db.query, "SELECT * FROM organized_records WHERE batch_id=? AND action='auto_move' AND undone=0"
                      " ORDER BY id DESC", (batch_id,))]
    if not rows:
        return {"ok": False, "msg": "没有可撤销的记录（可能已撤销或不存在）"}

    involved = {int(r["task_id"] or 0) for r in rows if r["task_id"]}
    stop_ids = [int(x) for x in (stop_rule_ids or []) if str(x).isdigit() and int(x) in involved]

    res = await pan.run_pan_thread(_undo_auto_move_sync, rows)

    disabled_rules: list[int] = []
    if res["undone"] > 0 and stop_ids:
        for rid in stop_ids:
            db.execute("UPDATE auto_move_rules SET enabled=0 WHERE id=?", (rid,))
            disabled_rules.append(rid)
        rem = db.query("SELECT count(*) c FROM auto_move_rules WHERE enabled=1", one=True)
        if not rem or int(rem["c"]) == 0:
            if bool(db.get_setting("auto_move_enabled", False)):
                db.set_setting("auto_move_enabled", False)
                logstream.warn("auto_move", "所有启用的整理规则已停用，定时文件整理总开关已自动关闭")

    logstream.info("auto_move",
                   f"撤销整理：恢复 {res['undone']} 条（失败 {res['failed']}，"
                   f"跳过 {len(res['skipped'])}），停用规则 {disabled_rules or '无'}")
    return {"ok": res["undone"] > 0 or bool(disabled_rules), "undone": res["undone"],
            "failed": res["failed"], "skipped": res["skipped"],
            "disabled_rules": disabled_rules}


async def auto_move_loop():
    """定时文件整理异步循环：按配置的间隔或固定时间点定期检查并排队执行移动规则。"""
    last_fixed_trigger_minute = ""
    while True:
        try:
            await asyncio.sleep(30)  # 每 30 秒巡检一次

            def _poll_auto_move_state():
                if not db.get_setting("auto_move_enabled", False):
                    return None
                if str(db.get_setting("running", False)).lower() != "true":
                    return None
                return (
                    str(db.get_setting("auto_move_schedule_type", "interval") or "interval"),
                    db.get_setting("auto_move_fixed_times", '["06:00", "12:00", "18:00"]'),
                    max(float(db.get_setting("auto_move_interval_hours", 6) or 6), 1.0),
                    float(db.get_setting("auto_move_last_run", 0) or 0),
                    transfer_gate.is_in_schedule_window(),
                )

            cfg = await asyncio.to_thread(_poll_auto_move_state)
            if not cfg:
                continue  # 功能未开启或系统总开关停止时不执行

            sched_type, fixed_times_raw, interval_hours, last_run, in_win = cfg
            should_trigger = False
            now = time.time()

            if sched_type == "fixed_times":
                try:
                    if isinstance(fixed_times_raw, str):
                        fixed_times = json.loads(fixed_times_raw)
                    elif isinstance(fixed_times_raw, list):
                        fixed_times = fixed_times_raw
                    else:
                        fixed_times = []
                except Exception:
                    fixed_times = []

                cur_hm = time.strftime("%H:%M")
                cur_min_key = time.strftime("%Y-%m-%d %H:%M")
                if cur_hm in fixed_times and cur_min_key != last_fixed_trigger_minute:
                    last_fixed_trigger_minute = cur_min_key
                    should_trigger = True
            else:
                if last_run == 0 or (now - last_run >= interval_hours * 3600):
                    should_trigger = True

            if should_trigger:
                if transfer_gate.is_exclusive_busy():
                    continue
                if not in_win:
                    logstream.info("auto_move", "⏰ 当前处于防风控时段外，自动文件整理暂停触发，将在进入允许时段后自动执行")
                    continue
                await execute_auto_move(trigger="scheduled")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logstream.warn("auto_move", f"定时整理循环异常: {e}")
