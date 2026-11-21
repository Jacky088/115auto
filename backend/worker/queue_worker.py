"""转存队列工作器：消费 transfers 表中 status='queued' 的任务。

可靠性设计：
- 提交前校验链接是否符合 115 支持范围（不符合 → 直接失败 + 日志 + TG 通知，不重试）
- 分享转存：API 成功后再反查目标目录确认文件真实到达（双重确认）
- 云下载：提交成功只标记"已提交"，由后台任务跟踪 115 真实下载结果后二次通知
- 风控控制：随机间隔、每日限额、指数退避重试、连续失败熔断
"""
import asyncio
import json
import random
import re
import time

from .. import database as db
from ..core import classifier, filters, logstream, transfer_gate
from ..pan115 import client as pan
from . import jobs

MAX_ATTEMPTS = 5

from .queue_guard import (  # noqa: F401
    PermanentTransferError,
    SPACE_GUARD_MIN_FREE,
    SPACE_GUARD_RATIO,
    SPACE_GUARD_RESUME_RATIO,
    SPACE_PROBE_TTL,
    _DEAD_SHARE_MARKERS,
    _NO_RETRY_SHARE_MARKERS,
    _SPACE_MARKERS,
    _SPACE_PROBE,
    _add_to_dir_cache,
    _archive_washed_version,
    _assert_still_running,
    _backoff,
    _dedup_by_name,
    _dir_cache,
    _fmt_gb,
    _get_dir_names_cached,
    _interval,
    _invalidate_dir_cache,
    _is_space_error,
    _norm_entry_name,
    _norm_filename,
    _num_setting,
    _space_gate,
    _space_preflight,
    _verify_receive,
    skip_if_disabled,
    validate_link,
)


def _process_one(job: dict):
    """同步执行一次转存（跑在线程池）。"""
    code, rc = job["share_code"], job["receive_code"]
    target = job["target_dir"]
    kind = job.get("kind", "share")

    # 0. 链接合法性：不符合 115 支持范围 → 确定性失败
    err = validate_link(kind, code, job["share_url"])
    if err:
        raise PermanentTransferError(err)

    # ---- 云下载任务（ED2K/磁力/HTTP）----
    if kind == "offline":
        cid = pan.mkdirs(target)
        if not cid:
            raise PermanentTransferError(f"目标目录无效: {target}")
        # 重试防重：上次提交可能已被 115 受理（响应超时/丢失），再提交会生成双任务
        if job.get("attempts", 0):
            try:
                key = jobs._offline_key(job["share_url"])
                tasks = pan.task_list_all()
                if any(jobs._offline_key(t.get("url") or "") == key for t in tasks):
                    logstream.info("transfer", "云下载任务已存在于 115（此前提交已受理），跳过重复提交")
                    changed = db.execute("UPDATE transfers SET status='submitted', finished_at=NULL"
                                         " WHERE id=? AND status='running'", (job["id"],))
                    if changed == 0:
                        return
                    display = classifier.headline(job["title"]) or job["title"][:80]
                    jobs.notify_transfer(display, job.get("category", ""), job.get("channel", ""),
                                         target, kind="offline")
                    return
            except Exception as e:
                # 查询失败时继续重新提交会生成双任务——这正是该防重检查要保护的窗口，
                # 与分享转存分支口径一致：抛重试异常进入退避，等查询恢复后再安全提交
                logstream.warn("transfer", f"云下载重试预检异常（稍后重试，避免重复提交双任务）: {e}")
                raise RuntimeError(f"云下载重试预检异常（防重复提交拦截）: {e}")
        _assert_still_running(job)  # 看门狗可能已重排：旧线程不得继续提交真实云下载任务
        resp = pan.offline_download(job["share_url"], cid)
        # 提交成功 ≠ 下载完成：标记 submitted，由 offline_check_loop 跟踪真实结果
        changed = db.execute("UPDATE transfers SET status='submitted', finished_at=NULL"
                             " WHERE id=? AND status='running'", (job["id"],))
        if changed == 0:
            return
        # 若 115 接口返回了 info_hash，写入 receive_code 方便后续精准核对跟踪
        ih = str((resp or {}).get("info_hash") or "").strip()
        if ih:
            db.execute("UPDATE transfers SET receive_code=? WHERE id=?", (ih, job["id"]))
        display = classifier.headline(job["title"]) or job["title"][:80]
        logstream.info("transfer",
                       f"☁️ 云下载任务已添加：{display[:60]} → {target}（下载完成后推送完成通知）")
        jobs.notify_transfer(display, job.get("category", ""), job.get("channel", ""),
                             target, kind="offline")
        return

    # ---- 分享转存任务 ----
    try:
        snap = pan.share_snap_all(code, rc)
    except pan.Pan115Error as e:
        # 分享已被取消/删除/不存在：确定性失败——重试无意义，也不计入连续失败熔断
        #（否则一批失效补全链接会把普通转存一起拖停 30 分钟）
        if any(m in str(e) for m in _DEAD_SHARE_MARKERS):
            raise PermanentTransferError(f"分享链接已失效（{str(e)[:120]}）") from e
        # 需要访问码但消息里没带（或带的码不对）：同一链接重试结果不变 → 直接失败
        if any(m in str(e) for m in _NO_RETRY_SHARE_MARKERS):
            raise PermanentTransferError("分享需要访问码，消息中未提供有效提取码，无法转存") from e
        raise

    # 1.45 超大分享截断透明化：超过 5000 文件的部分不会转存，任务与日志必须留痕
    if snap.get("truncated"):
        db.execute("UPDATE transfers SET error=? WHERE id=? AND status='running'",
                   ("分享文件数超过 5000 上限，仅转存前 5000 个文件", job["id"]))
        logstream.warn("transfer", f"分享 {code} 文件数超过 5000 上限，仅转存前 5000 个文件")
    # 名称优先取频道消息里的标题（已可能是 TMDB 官方名），分享内的文件夹名仅兜底
    title = (classifier.headline(job["title"]) or snap["title"] or job["title"][:100] or code)

    # 1.5 分享内没有任何文件（空目录/链接空壳）：无法转存，直接跳过而不是反复重试
    if not snap["files"]:
        reason = "分享内没有可转存的文件（可能为空目录或分享内容已失效）"
        db.execute("UPDATE transfers SET status='skipped', title=?, error=?"
                   " WHERE id=? AND status='running'", (title, reason, job["id"]))
        logstream.warn("transfer", f"分享 {code} 跳过：{reason}")
        return

    # 1.55 增量追更：同一分享此前已成功转存，分享方常往同一链接里持续加更集数
    #（今天 E01、明天 E01-E03）。本次按真实文件名与目标目录比对，只转存新增的文件，
    # 已存过的集数不重复提交（实测 115 对重复提交会生成 "(1)" 副本）。
    incr = bool(job.get("increment"))
    if incr and target:
        tops = [t for t in (snap.get("top_names") or []) if t]
        # 单顶层文件夹的分集分享：此前整包转存落在 target/<文件夹名>/ 下，
        # 新集数也应存回同一文件夹，保持目录结构一致
        if len(tops) == 1 and len(snap["files"]) > len(tops):
            target = f"{target.rstrip('/')}/{tops[0]}"
        existing: set[str] = set()
        try:
            dest_cid = pan.mkdirs(target)
            existing = pan.list_file_names(dest_cid)
        except Exception as e:
            logstream.warn("transfer", f"追更预检目标目录失败（本次全量提交，宁多勿漏）: {e}")
        existing_n = {_norm_filename(n) for n in existing}
        try:
            org_recs = db.query("SELECT orig_name, dest_name FROM organized_records WHERE undone=0 ORDER BY id DESC LIMIT 800")
            for rr in org_recs:
                on_n = _norm_filename(rr["orig_name"])
                dn_n = _norm_filename(rr["dest_name"])
                if on_n and len(on_n) >= 5 and not re.fullmatch(r"(?:s?\d+)?(?:e|ep)?\d+(?:\.[a-z0-9]+)?", on_n, re.I):
                    existing_n.add(on_n)
                if dn_n and len(dn_n) >= 5:
                    existing_n.add(dn_n)
        except Exception as e:
            logstream.warn("transfer", f"追更去重账本查询失败（本次按全量提交，可能产生重复副本）: {e}")
        new_files = [f for f in snap["files"]
                     if f.get("fn") and f.get("fid")
                     and _norm_filename(f["fn"]) not in existing_n]
        new_size = sum(int(f.get("fs") or 0) for f in new_files)
        if not new_files:
            db.execute("UPDATE transfers SET status='skipped', title=?, size=0, error=?"
                       " WHERE id=? AND status='running'",
                       (title, "追更检查：分享无新增文件（已存过的集数不重复转存）", job["id"]))
            logstream.info("transfer", f"分享 {code} 追更检查完成：无新增文件，跳过")
            return
        total_count = len(snap["files"])
        # 用「仅新增文件」替换快照：后续空间预检/黑名单/提交/反查都只针对新增部分
        snap["files"] = new_files
        snap["total_size"] = new_size
        snap["top_names"] = []
        snap["top_ids"] = []
        logstream.info("transfer",
                       f"分享 {code} 追更检查：分享共 {total_count} 个文件，"
                       f"检测到新增 {len(new_files)} 个（{new_size / (1 << 30):.2f} GB）")

    # 1.6 空间预检：115 剩余空间不足时跳过（转存前拦截）
    if not _space_preflight(snap["total_size"], title):
        db.execute("UPDATE transfers SET status='skipped', title=?, size=?, file_count=?, "
                   "error=? WHERE id=? AND status='running'",
                   (title, snap["total_size"], len(snap["files"]),
                    f"115 剩余空间不足（需要 {_fmt_gb(snap['total_size'])}）", job["id"]))
        logstream.warn("transfer",
                       f"分享 {code} 跳过：115 剩余空间不足"
                       f"（需要 {_fmt_gb(snap['total_size'])}），任务已标记为「跳过」")
        return

    # 1.7 同名去重（可关）：目标目录已有同名内容时不重复占空间
    # 目录在这里解析一次（cid 供去重与转存两用），解析失败留给第 3 步正式报错走重试
    cid = 0
    if target:
        try:
            cid = pan.mkdirs(target)
        except Exception:
            cid = 0
    dedup_reason = "" if incr else _dedup_by_name(cid, title, snap)
    # 追更任务不做同名去重：增量分支已按真实文件名比对过，标题前缀匹配反而可能误拦
    if dedup_reason:
        db.execute("UPDATE transfers SET status='duplicate', title=?, size=?, error=?"
                   " WHERE id=?",
                   (title, snap["total_size"], dedup_reason, job["id"]))
        logstream.info("transfer", f"分享 {code} 跳过：{dedup_reason}")
        return

    # 2. 内容黑名单：文件名 + 体积（放行任务跳过）
    if not job.get("bypass_filters"):
        blocked, rule_type, rule_value = filters.check_files(
            [f.get("fn", "") for f in snap["files"]], snap["total_size"])
        if blocked:
            filters.record_hit(job["channel"], job["message_id"], title, code,
                               rule_type, rule_value, "share_info")
            db.execute("UPDATE transfers SET status='blocked', title=?, size=?, file_count=?, "
                       "error=? WHERE id=?",
                       (title, snap["total_size"], len(snap["files"]),
                        f"{rule_type}:{rule_value}", job["id"]))
            logstream.warn("transfer", f"分享 {code} 被内容黑名单拦截（{rule_type}:{rule_value}）")
            return

    # 3. 确保目标目录存在（上面解析成功则直接复用）
    if not cid:
        cid = pan.mkdirs(target)
    if not cid:
        raise RuntimeError(f"目标目录无效: {target}")

    # 4. 转存（115 对重复文件会报"已存在"，同样视为已在目标目录）
    # 分享文件字段可能因接口形态不同而缺失（fid/file_id），统一过滤后再提交，
    # 避免旧的直取 f["fid"] 在字段不匹配时抛出 KeyError（历史线上报错 'fid'）。
    file_total = len(snap["files"])
    flat_ids = [str(f.get("fid")) for f in snap["files"] if f.get("fid")]
    # 优先按分享顶层条目整包转存：分享是文件夹（剧集/合集）时保留目录结构，
    # 与 115 网页的“转存”一致；逐个子文件 id 提交在大量文件时（如 5 季 100 集、
    # 316GB 的剧集分享）容易被 115 拒绝或只转存一部分。
    top_ids = [str(i) for i in (snap.get("top_ids") or []) if i]
    file_ids = top_ids or flat_ids
    if not file_ids:
        raise RuntimeError("分享内文件缺少有效的文件 id（115 接口返回字段异常），需要重试")
    # 3.8 重试防重：此前尝试可能已实际落盘（接口成功但反查超时/落库延迟）。
    # 按分享的真实文件名预检目标目录，命中则跳过提交——实测 115 对重试任务的
    # 再接收会放行并生成 "(1)" 副本，浪费空间；文件已在即视为成功，不再提交。
    skip_receive = False
    if job.get("attempts", 0):
        try:
            existing = pan.list_file_names(cid)
        except Exception as e:
            # 修复漏洞 1：网络抖动导致无法读取目录确认状态时，绝不盲目二次提交导致生成 (1) 副本；
            # 抛出重试异常进入退避，等待网络恢复后再安全校验
            logstream.warn("transfer", f"重试预检目标目录网络异常（稍后重试，避免盲目提交产生 (1) 副本）: {e}")
            raise RuntimeError(f"重试预检目标目录网络异常（防重复转存拦截）: {e}")
        existing_n = {_norm_filename(n) for n in existing}
        pre = [f.get("fn") for f in snap["files"][:10] if f.get("fn")]
        pre += (snap.get("top_names") or [])[:10]
        pre = [n for n in dict.fromkeys(pre) if n]
        if pre and any(_norm_filename(n) in existing_n for n in pre):
            skip_receive = True
            logstream.info("transfer",
                           f"分享 {code} 目标目录已存在本次分享的文件（此前尝试已落盘），跳过重复提交")

    confirmed = skip_receive  # 预检命中 = 已确认文件就在目标目录
    if not skip_receive:
        _assert_still_running(job)  # 看门狗可能已重排：旧线程不得继续执行真实 115 写操作
        try:
            pan.receive(code, rc, cid, file_ids)
        except pan.Pan115Error as e:
            msg = str(e)
            if any(m in msg for m in _DEAD_SHARE_MARKERS):
                # 提交阶段也可能报链接失效/过期：确定性失败，不重试不计熔断
                raise PermanentTransferError(f"分享链接已失效（{msg[:120]}）") from e
            if any(m in msg for m in _NO_RETRY_SHARE_MARKERS):
                # 提交阶段才要求访问码（如码在提交时校验失败）：重试无用，直接失败
                raise PermanentTransferError("分享需要访问码，消息中未提供有效提取码，无法转存") from e
            if "已接收" in msg:
                # 115 幂等信号："文件已接收，无需重复接收"——本账号此前已接收过该分享，
                # 文件已在网盘中。直接按成功处理：不反查、不找回、不重试。
                confirmed = True
                logstream.info("transfer", f"分享 {code} 115 提示此前已接收，文件已在网盘中，按成功处理")
            elif "已存在" in msg and "不存在" not in msg:
                # "文件已存在"：目标目录已有同名文件 → 走反查确认。
                # 注意不能宽泛匹配「存在」：「目录不存在」「分享文件不存在」会被误判，
                # 触发无意义的全盘找回，甚至把网盘其他目录的同名文件错误复制进来
                logstream.info("transfer", f"分享 {code} 文件已存在于目标目录，视为已转存")
            elif top_ids and flat_ids and set(flat_ids) != set(top_ids):
                # 极少数接口形态不接受目录 id：退回逐文件提交
                logstream.warn("transfer",
                               f"分享 {code} 顶层整包转存被拒（{msg[:120]}），改为逐文件提交")
                pan.receive(code, rc, cid, flat_ids)
            else:
                raise

    # 5. 双重确认：反查目标目录，确认文件真实到达（防接口误报）。
    # 已确认文件在目标目录的（重试预检命中 / 已接收信号）除外。
    found: dict = {}
    if not confirmed and not _verify_receive(cid, snap, found=found):
        # 严格禁止跨目录全盘搜索复制：115 搜索是分词模糊匹配，全盘找回会把其他影视目录中同集数/同名的文件
        # 错误复制进目标目录（严重污染媒体库并产生 (1) 冲突副本）。
        # 此处如未查到，多为 115 落库延迟，直接抛出重试异常；60 秒后工作器重试时
        # 会通过 pre-check（已有同名文件）自动命中并标记成功，零跨目录污染。
        _assert_still_running(job)
        raise RuntimeError("转存接口已返回成功，但反查目标目录暂未查到文件（多为 115 落库延迟，"
                           "系统将自动重试确认，无需手动处理）；多次重试仍失败时，"
                           "请到 115 网页核对目标目录或检查回收站")

    dest_ids = json.dumps([str(i) for i in (found.get("ids") or [])], ensure_ascii=False) \
        if found.get("ids") else ""
    changed = db.execute("UPDATE transfers SET status='success', title=?, size=?, file_count=?, "
                         "dest_cid=?, dest_ids=?, finished_at=?, running_at=NULL, next_retry_at=NULL"
                         " WHERE id=? AND status='running'",
                         (title, snap["total_size"], file_total, cid, dest_ids,
                          time.time(), job["id"]))
    if changed == 0:
        # 看门狗已把本任务重新排队（或已被人工终止）：旧线程迟到返回不能再改终态，
        # 更不能二次计额度/二次通知，否则会与新一轮执行重复转存、重复扣额度。
        logstream.warn("transfer",
                       f"任务 #{job['id']} 已不在 running 状态，忽略本次迟到成功回写（防重复计额）")
        return
    _invalidate_dir_cache(cid)
    db.quota_incr()
    pan.note_115_ok()  # 完整成功：风控退避逐级恢复
    logstream.info("transfer",
                   f"转存成功: {title[:50]} → {target}（{file_total} 个文件, "
                   f"{snap['total_size'] / (1 << 30):.2f} GB）")
    jobs.notify_transfer(title, job.get("category", ""), job.get("channel", ""),
                         target, size=snap["total_size"], kind="share",
                         file_count=file_total)
    jobs.check_space_alert(f"转存「{title[:40]}」后（本次 {snap['total_size'] / (1 << 40):.2f} TB）")
    if job.get("wash") and job.get("wash_from"):
        _archive_washed_version(job)
    # 说明：转存后统一通过「整理刮削」队列互斥执行整理重命名，此处不再直接并发调 organize_seasons。


def _fail(job: dict, err: str):
    attempts = job["attempts"] + 1
    # 空间不足：账号级环境问题，重试必然继续失败。本条标 skipped（可恢复）不重试，
    # 同时挂起整个队列，等空间恢复后自动继续，避免后续任务被逐条打成 failed。
    if _is_space_error(err):
        db.execute("UPDATE transfers SET status='skipped', attempts=?, error=?"
                   " WHERE id=? AND status='running'",
                   (attempts, f"SPACE_FULL: {err}"[:500], job["id"]))
        db.set_setting("space_blocked", True)
        db.set_setting("space_blocked_reason", str(err)[:200])
        logstream.warn("transfer",
                       f"分享 {job['share_code']} 因 115 空间不足跳过（不重试），"
                       "转存队列已暂停，等待空间恢复")
        jobs.notify_failure(
            job.get("kind", "share"),
            (classifier.headline(job.get("title", "")) or job.get("title", "")
             or job["share_code"]), job.get("category", ""), job.get("channel", ""),
            "115 空间不足",
            retry_info="💡 本条不重试，已标记「跳过」；转存队列已自动暂停，清理空间后会恢复")
        jobs.check_space_alert("转存时 115 返回空间不足")
        return
    if attempts >= MAX_ATTEMPTS:
        db.execute("UPDATE transfers SET status='failed', attempts=?, error=? WHERE id=?"
                   " AND status='running'",
                   (attempts, err[:500], job["id"]))
        logstream.error("transfer", f"分享 {job['share_code']} 转存失败（已达最大重试）: {err}")
        kind = job.get("kind", "share")
        jobs.notify_failure(
            kind, (classifier.headline(job.get("title", "")) or job.get("title", "")
                   or job["share_code"]), job.get("category", ""),
            job.get("channel", ""), err[:200],
            retry_info=f"💡 已自动重试 {attempts} 次仍未成功，请检查分享链接是否失效")
        jobs.check_space_alert("转存失败后（若提示空间不足请及时清理）")
    else:
        delay = _backoff(attempts)
        changed = db.execute("UPDATE transfers SET status='queued', attempts=?, error=?, next_retry_at=?"
                             " WHERE id=? AND status='running'",
                             (attempts, err[:500], time.time() + delay, job["id"]))
        if changed == 0:
            return  # 已被看门狗重排/人工终止：不再覆盖当前状态
        logstream.warn("transfer",
                       f"分享 {job['share_code']} 第 {attempts} 次失败，{delay} 秒后重试：{err}")


def _retry_scan():
    """把到期的重试任务重新置为可执行（由 fetch 领取）。"""
    db.execute("UPDATE transfers SET next_retry_at=NULL WHERE status='queued' AND next_retry_at<=?",
               (time.time(),))


def _fetch_next():
    """领取下一个转存任务：优先级高的先跑（向前补全 priority=1，优先消耗每日额度），
    同级按入队先后（FIFO）。

    排空/独占期仅领取水线之前的任务：水线之后（排空开始后 TG 新入库）的任务
    留在队列里等待整理/刮削结束再继续——否则新任务会在排空期被继续消费，
    既虚耗每日额度又拉长独占等待（与 transfer_gate.get_drain_status 口径一致）。
    """
    _retry_scan()
    cutoff = transfer_gate.drain_cutoff()
    if cutoff:
        return db.query(
            "SELECT * FROM transfers WHERE status='queued'"
            " AND (next_retry_at IS NULL OR next_retry_at<=?) AND id<=?"
            " ORDER BY priority DESC, id ASC LIMIT 1", (time.time(), cutoff), one=True)
    return db.query(
        "SELECT * FROM transfers WHERE status='queued' AND (next_retry_at IS NULL OR next_retry_at<=?)"
        " ORDER BY priority DESC, id ASC LIMIT 1", (time.time(),), one=True)


async def _heartbeat(job_id: int):
    """长任务心跳：每 60 秒刷新 running_at 与工作器心跳，防大任务被看门狗误判。"""
    while True:
        await asyncio.sleep(60)
        mark_worker_alive()
        await asyncio.to_thread(
            db.execute,
            "UPDATE transfers SET running_at=? WHERE id=? AND status='running'",
            (time.time(), job_id),
        )


def _in_schedule_window() -> bool:
    """定时计划：开启后仅允许时段内转存与云下载（由 transfer_gate 统一判定）。"""
    return transfer_gate.is_in_schedule_window()


def _poll_worker_gates():
    """在线程池中集中读取工作器调度所需的设置与额度状态，避免事件循环争抢 SQLite 锁。"""
    running = bool(db.get_setting("running", False))
    paused = bool(db.get_setting("paused", False))
    sched_on = bool(db.get_setting("schedule_enabled", False))
    in_win = _in_schedule_window()
    jitter_min = int(float(db.get_setting("schedule_jitter_min", 5) or 0))
    limit = max(1, int(_num_setting("daily_limit", 50)))
    quota = db.quota_today() if limit > 0 else 0
    waiting = (
        db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
        if (limit > 0 and quota >= limit) else 0
    )
    space_blocked, space_reason = _space_gate()
    return (running, paused, sched_on, in_win, jitter_min, limit, quota, waiting,
            space_blocked, space_reason)


def _claim_next_job():
    """扫描重试并发原子抢占下一个可执行任务；已禁用类型则直接跳过并返回哨兵值。"""
    _retry_scan()
    job = _fetch_next()
    if not job:
        return None
    job = dict(job)
    if skip_if_disabled(job):
        return "skipped"
    changed = db.execute(
        "UPDATE transfers SET status='running', running_at=?, error='', next_retry_at=NULL"
        " WHERE id=? AND status='queued'",
        (time.time(), job["id"]),
    )
    if changed == 0:
        return "race"
    job["status"] = "running"
    return job


_worker_heartbeat_at: float = 0.0
_worker_sleeping_until: float = 0.0
_worker_task: asyncio.Task | None = None


def mark_worker_alive():
    """刷新工作器主循环心跳时间戳。"""
    global _worker_heartbeat_at
    _worker_heartbeat_at = time.time()


def mark_worker_sleeping(seconds: float):
    """标记工作器处于合法的休眠阶段（扰动/小憩/限额退避），避免看门狗误判为卡死。"""
    global _worker_sleeping_until
    _worker_sleeping_until = time.time() + max(0.0, float(seconds))


def get_worker_task() -> asyncio.Task | None:
    return _worker_task


def set_worker_task(task: asyncio.Task | None):
    global _worker_task
    _worker_task = task


def is_worker_stalled(threshold_seconds: float = 600.0) -> bool:
    """判断转存工作器是否失联卡死：已启动且超过阈值无心跳且未处于计划休眠与转存活跃中。"""
    now = time.time()
    if now < _worker_sleeping_until:
        return False
    if transfer_gate.is_transfer_active():
        return False
    if _worker_heartbeat_at <= 0.0:
        return False
    effective_heartbeat = max(_worker_heartbeat_at, _worker_sleeping_until)
    return (now - effective_heartbeat) > threshold_seconds


def restart_worker_task() -> asyncio.Task:
    """重启转存工作器协程并更新全局引用（供主进程启动与看门狗自愈复用）。"""
    global _worker_task
    if _worker_task and not _worker_task.done():
        try:
            _worker_task.cancel()
        except Exception:
            pass
    _worker_task = asyncio.create_task(queue_worker())
    mark_worker_alive()
    return _worker_task


async def queue_worker():
    logstream.info("transfer", "转存工作器启动（等待「开始运行」）")
    consecutive_fails = 0
    consecutive_success = 0
    schedule_logged = False
    pause_logged = False
    stopped_logged = False
    space_logged = False
    last_schedule_active = False
    # 启动时清掉上次进程遗留的挂起标记：门闸会在首轮重新探测，避免误显示「空间不足」
    if db.get_setting("space_blocked", False):
        db.set_setting("space_blocked", False)
        db.set_setting("space_blocked_reason", "")
    while True:
        try:
            mark_worker_alive()
            # 互斥门闸：若文件整理正在运行，暂停转存并在此挂起等待整理完成
            await transfer_gate.wait_transfer_worker_allowed()

            try:
                gates = await asyncio.wait_for(asyncio.to_thread(_poll_worker_gates), timeout=15.0)
            except asyncio.TimeoutError:
                logstream.warn("transfer", "工作器门禁状态轮询超时（>15s），跳过本轮等待下次检测")
                await asyncio.sleep(5)
                continue
            (running, paused, sched_on, in_win, jitter_min, limit, quota, waiting,
             space_blocked, space_reason) = gates

            # 总开关：用户未点击「开始运行」前不消费队列
            if not running:
                if not stopped_logged:
                    logstream.info("transfer", "系统未启动（向导完成后点击网页顶栏「开始运行」）")
                    stopped_logged = True
                await asyncio.sleep(5)
                continue
            stopped_logged = False

            # 全局暂停（TG /pause 或设置）
            if paused:
                if not pause_logged:
                    logstream.warn("transfer", "转存已暂停（TG 发送 /resume 恢复）")
                    pause_logged = True
                await asyncio.sleep(10)
                continue
            pause_logged = False

            # 空间门闸：115 空间告急时整队列挂起，任务保持 queued 不判失败、不计重试次数。
            # 空间恢复（<88%）后自动继续，避免逐条失败刷屏与无意义的 115 调用。
            if space_blocked:
                if not space_logged:
                    logstream.warn("transfer",
                                   f"115 空间告急（{space_reason}），转存队列已自动暂停；"
                                   "任务保留在队列，清理空间后自动恢复")
                    await asyncio.to_thread(db.set_setting, "space_blocked", True)
                    await asyncio.to_thread(db.set_setting, "space_blocked_reason", space_reason)
                    await asyncio.to_thread(jobs.check_space_alert,
                                            f"转存队列已暂停（{space_reason}）")
                    space_logged = True
                await asyncio.sleep(60)
                continue
            if space_logged:
                logstream.info("transfer", "115 空间已恢复，转存队列自动继续")
                space_logged = False
                await asyncio.to_thread(db.set_setting, "space_blocked", False)
                await asyncio.to_thread(db.set_setting, "space_blocked_reason", "")
                await asyncio.to_thread(jobs.notify, "✅ 115 空间已恢复，转存队列自动继续。")

            # 定时计划窗口检查
            if not in_win:
                last_schedule_active = False
                if not schedule_logged:
                    logstream.info("transfer", "当前不在允许转存时段，新任务暂不入队"
                                               "（可在设置页调整或关闭定时计划）")
                    schedule_logged = True
                await asyncio.sleep(60)
                continue
            schedule_logged = False

            # 从「时段外」刚进入「时段内」时触发拟人化开工随机扰动（防整点脚本特征）
            if sched_on and not last_schedule_active:
                last_schedule_active = True
                if jitter_min > 0:
                    jitter_sec = random.randint(15, max(15, jitter_min * 60))
                    logstream.info("transfer", f"⏰ 进入允许转存时段，开启拟人化开工随机扰动（等待 {jitter_sec} 秒后开始处理队列）...")
                    mark_worker_sleeping(jitter_sec)
                    await asyncio.sleep(jitter_sec)
                    mark_worker_alive()
            elif not sched_on:
                last_schedule_active = False

            # 旧库可能存过 0（旧的「不限制」语义）：现约定限额最小 1，兜底夹紧
            if limit > 0 and quota >= limit:
                logstream.warn("transfer",
                               f"已达每日限额 {limit}（分享+云下载共享额度），剩余 {waiting} 个任务保留在队列，"
                               "明日 0 点额度重置后自动接续转存")
                mark_worker_sleeping(600)
                await asyncio.sleep(600)
                mark_worker_alive()
                continue

            # 领取任务前再次确认互斥门闸
            await transfer_gate.wait_transfer_worker_allowed()
            claimed = await asyncio.to_thread(_claim_next_job)
            if claimed is None:
                await asyncio.sleep(5)
                continue
            if claimed == "skipped":
                await asyncio.sleep(1)
                continue
            if claimed == "race":
                await asyncio.sleep(0.5)
                continue
            job = claimed

            # cookie 检查（不消耗重试次数：长时间失效不应把排队任务打成 failed）
            st = await pan.run_pan_thread(pan.check_login)
            if not st.get("ok"):
                err = f"115 未登录或 Cookie 失效：{st.get('err', '')}"
                await asyncio.to_thread(
                    db.execute,
                    "UPDATE transfers SET status='queued', error=?, next_retry_at=?"
                    " WHERE id=? AND status='running'",
                    (err[:500], time.time() + 60, job["id"]),
                )
                logstream.warn("transfer",
                               f"115 Cookie 失效，任务 #{job['id']} 暂缓 60 秒（不计入重试次数）")
                await asyncio.sleep(60)
                continue

            # 长任务心跳：转存在线程池中运行且无法取消，心跳确保 running_at 持续刷新，
            # 避免看门狗把仍在正常推进的大分享误判为卡死并重排（进而重复转存）
            hb = asyncio.create_task(_heartbeat(job["id"]))
            transfer_gate.mark_transfer_active(True)
            try:
                await pan.run_pan_thread(_process_one, job)
                consecutive_fails = 0
                consecutive_success += 1

                # 拟人化连存小憩机制
                def _batch_rest_cfg():
                    en = bool(db.get_setting("batch_rest_enabled", True))
                    cnt = max(1, int(float(db.get_setting("batch_rest_count", 3) or 3)))
                    min_m = max(1, int(float(db.get_setting("batch_rest_min_min", 2) or 2)))
                    max_m = max(min_m, int(float(db.get_setting("batch_rest_max_min", 5) or 5)))
                    return en, cnt, min_m, max_m

                rest_on, rest_count, min_m, max_m = await asyncio.to_thread(_batch_rest_cfg)
                if rest_on:
                    if consecutive_success >= rest_count:
                        consecutive_success = 0
                        rest_sec = random.randint(min_m * 60, max_m * 60)
                        logstream.info("transfer", f"☕ 已连续完成 {rest_count} 笔转存，触发拟人化小憩（休息 {rest_sec // 60} 分 {rest_sec % 60} 秒后继续）...")
                        mark_worker_sleeping(rest_sec)
                        await asyncio.sleep(rest_sec)
                        mark_worker_alive()
            except PermanentTransferError as e:
                # 确定性失败（格式不支持/地址无效等）：不重试，立即失败并通知
                await asyncio.to_thread(
                    db.execute,
                    "UPDATE transfers SET status='failed', attempts=?, error=? WHERE id=?"
                    " AND status='running'",
                    (job["attempts"] + 1, str(e)[:500], job["id"]),
                )
                logstream.error("transfer", f"任务永久失败: {str(e)}")
                pan.note_115_error(str(e))
                await asyncio.to_thread(
                    jobs.notify_failure, job.get("kind", "share"),
                    classifier.headline(job.get("title", "")) or job.get("title", ""),
                    job.get("category", ""), job.get("channel", ""),
                    str(e), retry_info="💡 该链接无法被 115 处理，不会自动重试")
                consecutive_fails = 0  # 任务自身问题不计入熔断
            except Exception as e:
                consecutive_fails += 1
                consecutive_success = 0
                pan.note_115_error(str(e))
                await asyncio.to_thread(_fail, job, str(e))
                if consecutive_fails >= 5:
                    logstream.error("transfer", "连续失败 5 次，暂停转存 30 分钟")
                    mark_worker_sleeping(1800)
                    await asyncio.sleep(1800)
                    mark_worker_alive()
                    consecutive_fails = 0
            finally:
                transfer_gate.mark_transfer_active(False)
                hb.cancel()
                mark_worker_alive()

            wait_sec = await asyncio.to_thread(_interval)
            mark_worker_sleeping(wait_sec)
            await asyncio.sleep(wait_sec)
            mark_worker_alive()
        except Exception as e:
            logstream.error("transfer", f"工作器异常: {logstream.exc(e)}")
            await asyncio.sleep(30)
