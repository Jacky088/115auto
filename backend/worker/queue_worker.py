"""转存队列工作器：消费 transfers 表中 status='queued' 的任务。

可靠性设计：
- 提交前校验链接是否符合 115 支持范围（不符合 → 直接失败 + 日志 + TG 通知，不重试）
- 分享转存：API 成功后再反查目标目录确认文件真实到达（双重确认）
- 云下载：提交成功只标记"已提交"，由后台任务跟踪 115 真实下载结果后二次通知
- 风控控制：随机间隔、每日限额、指数退避重试、连续失败熔断
"""
import asyncio
import random
import re
import time

from .. import database as db
from ..core import filters, logstream
from ..pan115 import client as pan
from . import jobs

MAX_ATTEMPTS = 5


class PermanentTransferError(Exception):
    """确定性失败（如链接格式不支持），不重试，直接失败并通知。"""


def validate_link(kind: str, share_code: str, share_url: str) -> str:
    """校验链接是否符合 115 支持范围，返回错误原因；合法返回空串。"""
    if kind == "share":
        code = (share_code or "").strip()
        if not code:
            return "分享码为空，链接无效"
        if not re.fullmatch(r"[0-9A-Za-z]{5,24}", code):
            return f"分享码格式不符合 115 规范（{code[:24]}），无法转存"
        return ""
    # 云下载
    url = (share_url or "").strip()
    if not url:
        return "云下载地址为空"
    if url.startswith("magnet:?xt=urn:btih:"):
        h = url.split("btih:", 1)[1].split("&", 1)[0]
        if re.fullmatch(r"[0-9a-fA-F]{40}", h) or re.fullmatch(r"[A-Za-z2-7]{32}", h):
            return ""
        return "磁力链 hash 格式不支持（115 仅支持 BTv1：40 位十六进制或 32 位 Base32），无法云下载"
    if url.startswith("ed2k://|file|"):
        parts = url.split("|")
        if len(parts) >= 5 and re.fullmatch(r"[0-9A-Fa-f]{32}", parts[4]):
            return ""
        return "电驴链接格式不完整（缺少有效的文件 hash），无法云下载"
    if url.lower().startswith(("http://", "https://", "ftp://")):
        return ""
    return "不支持的链接协议（115 云下载仅支持 HTTP/FTP/磁力/电驴），无法云下载"


def _interval() -> float:
    lo = int(db.get_setting("transfer_interval_min", 30))
    hi = int(db.get_setting("transfer_interval_max", 90))
    return random.uniform(lo, max(hi, lo))


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
        pan.offline_download(job["share_url"], cid)
        # 提交成功 ≠ 下载完成：标记 submitted，由 offline_check_loop 跟踪真实结果
        db.execute("UPDATE transfers SET status='submitted', finished_at=NULL WHERE id=?",
                   (job["id"],))
        logstream.info("transfer",
                       f"☁️ 云下载任务已添加（①）: {job['title'][:60]} → {target}（已提交 115，下载完成后推送第②条通知）")
        jobs.notify_transfer(job["title"][:80], job.get("category", ""), job.get("channel", ""),
                             target, kind="offline")
        return

    # ---- 分享转存任务 ----
    snap = pan.share_snap_all(code, rc)
    title = snap["title"] or job["title"][:100] or code

    # 2. 内容黑名单：文件名 + 体积（放行任务跳过）
    if not job.get("bypass_filters"):
        blocked, rule_type, rule_value = filters.check_files(
            [f["fn"] for f in snap["files"]], snap["total_size"])
        if blocked:
            filters.record_hit(job["channel"], job["message_id"], title, code,
                               rule_type, rule_value, "share_info")
            db.execute("UPDATE transfers SET status='blocked', title=?, size=?, file_count=?, "
                       "error=? WHERE id=?",
                       (title, snap["total_size"], len(snap["files"]),
                        f"{rule_type}:{rule_value}", job["id"]))
            logstream.warn("transfer", f"分享 {code} 被内容黑名单拦截（{rule_type}:{rule_value}）")
            return

    # 3. 确保目标目录存在
    cid = pan.mkdirs(target)
    if not cid:
        raise RuntimeError(f"目标目录无效: {target}")

    # 4. 转存（115 对重复文件会报"已存在"，同样视为已在目标目录）
    file_ids = [f["fid"] for f in snap["files"]]
    try:
        pan.receive(code, rc, cid, file_ids)
    except pan.Pan115Error as e:
        if "存在" not in str(e):
            raise
        logstream.info("transfer", f"分享 {code} 文件已存在于目标目录，视为已转存")

    # 5. 双重确认：反查目标目录，确认文件真实到达（防接口误报）
    if not _verify_receive(cid, snap):
        raise RuntimeError("转存接口返回成功，但目标目录未查到文件，需要复核后重试")

    db.execute("UPDATE transfers SET status='success', title=?, size=?, file_count=?, "
               "finished_at=? WHERE id=?",
               (title, snap["total_size"], len(file_ids), time.time(), job["id"]))
    db.quota_incr()
    logstream.info("transfer",
                   f"转存成功: {title[:50]} → {target}（{len(file_ids)} 个文件, "
                   f"{snap['total_size'] / (1 << 30):.2f} GB）")
    jobs.notify_transfer(title, job.get("category", ""), job.get("channel", ""),
                         target, size=snap["total_size"], kind="share",
                         file_count=len(file_ids))


def _verify_receive(cid: int, snap: dict) -> bool:
    """反查目标目录：抽查分享文件名是否真实到达。验证接口异常时不误判（信任 API 结果）。"""
    names = [f["fn"] for f in snap["files"][:10] if f.get("fn")]
    if not names:
        return True
    time.sleep(2)
    try:
        got = pan.list_file_names(cid)
        return any(n in got for n in names)
    except Exception as e:
        logstream.warn("transfer", f"目录反查失败（按 API 成功处理）: {e}")
        return True


def _fail(job: dict, err: str):
    attempts = job["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        db.execute("UPDATE transfers SET status='failed', attempts=?, error=? WHERE id=?",
                   (attempts, err[:500], job["id"]))
        logstream.error("transfer", f"分享 {job['share_code']} 转存失败（已达最大重试）: {err}")
        kind = job.get("kind", "share")
        jobs.notify_failure(
            kind, job.get("title", "") or job["share_code"], job.get("category", ""),
            job.get("channel", ""), err[:200],
            retry_info=f"💡 已自动重试 {attempts} 次仍未成功，请检查分享链接是否失效")
    else:
        backoffs = db.get_setting("retry_backoff", [60, 300, 1800])
        delay = backoffs[min(attempts - 1, len(backoffs) - 1)]
        db.execute("UPDATE transfers SET status='queued', attempts=?, error=?, next_retry_at=? WHERE id=?",
                   (attempts, err[:500], time.time() + delay, job["id"]))
        logstream.warn("transfer",
                       f"分享 {job['share_code']} 第 {attempts} 次失败，{delay}s 后重试: {err}")


def _retry_scan():
    """把到期的重试任务重新置为可执行（由 fetch 领取）。"""
    db.execute("UPDATE transfers SET next_retry_at=NULL WHERE status='queued' AND next_retry_at<=?",
               (time.time(),))


def skip_if_disabled(job: dict) -> bool:
    """功能开关检查：任务所属类型被关闭时标记 skipped。返回是否已跳过。"""
    kind = job.get("kind", "share")
    enabled = db.get_setting("enable_offline_download" if kind == "offline"
                             else "enable_share_transfer", True)
    if enabled:
        return False
    label = "云下载功能已关闭" if kind == "offline" else "分享转存功能已关闭"
    db.execute("UPDATE transfers SET status='skipped', error=? WHERE id=?", (label, job["id"]))
    logstream.info("transfer", f"任务 {job['share_code']} 跳过：{label}")
    return True


def _in_schedule_window() -> bool:
    """定时计划：开启后仅允许时段内转存（支持跨零点，如 22:00-08:00）。"""
    if not db.get_setting("schedule_enabled", False):
        return True
    from datetime import datetime
    now = datetime.now().strftime("%H:%M")
    start = str(db.get_setting("schedule_start", "22:00"))
    end = str(db.get_setting("schedule_end", "08:00"))
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # 跨零点


async def queue_worker():
    logstream.info("transfer", "转存工作器启动（等待「开始运行」）")
    consecutive_fails = 0
    schedule_logged = False
    pause_logged = False
    stopped_logged = False
    while True:
        try:
            # 总开关：用户未点击「开始运行」前不消费队列
            if not db.get_setting("running", False):
                if not stopped_logged:
                    logstream.info("transfer", "系统未启动（向导完成或账号就绪后点「开始运行」）")
                    stopped_logged = True
                await asyncio.sleep(5)
                continue
            stopped_logged = False

            # 全局暂停（TG /pause 或设置）
            if db.get_setting("paused", False):
                if not pause_logged:
                    logstream.warn("transfer", "转存已暂停（发送 /resume 恢复）")
                    pause_logged = True
                await asyncio.sleep(10)
                continue
            pause_logged = False

            # 定时计划窗口检查
            if not _in_schedule_window():
                if not schedule_logged:
                    logstream.info("transfer", "当前不在允许转存时段内，任务暂停排队"
                                               "（设置页可调整或关闭定时计划）")
                    schedule_logged = True
                await asyncio.sleep(60)
                continue
            schedule_logged = False

            limit = int(db.get_setting("daily_limit", 50))
            if limit > 0 and db.quota_today() >= limit:
                logstream.warn("transfer", f"已达每日限额 {limit}，明日 0 点自动恢复")
                await asyncio.sleep(600)
                continue

            _retry_scan()
            job = db.query(
                "SELECT * FROM transfers WHERE status='queued' AND (next_retry_at IS NULL OR next_retry_at<=?)"
                " ORDER BY id ASC LIMIT 1", (time.time(),), one=True)
            if not job:
                await asyncio.sleep(5)
                continue

            job = dict(job)
            # 功能开关：已关闭类型的排队任务直接跳过（无 115 调用，快速流转）
            if skip_if_disabled(job):
                await asyncio.sleep(1)
                continue
            # 与其他 queued 抢占：置 running
            changed = db.execute("UPDATE transfers SET status='running', error='' WHERE id=? AND status='queued'",
                                 (job["id"],))
            if changed == 0:
                await asyncio.sleep(0.5)
                continue
            job["status"] = "running"

            # cookie 检查（快速失败）
            st = await asyncio.to_thread(pan.check_login)
            if not st.get("ok"):
                _fail(job, f"115 未登录或 cookie 失效: {st.get('err', '')}")
                await asyncio.sleep(60)
                continue

            try:
                await asyncio.to_thread(_process_one, job)
                consecutive_fails = 0
            except PermanentTransferError as e:
                # 确定性失败（格式不支持/地址无效等）：不重试，立即失败并通知
                db.execute("UPDATE transfers SET status='failed', attempts=?, error=? WHERE id=?",
                           (job["attempts"] + 1, str(e)[:500], job["id"]))
                logstream.error("transfer", f"任务永久失败: {str(e)}")
                jobs.notify_failure(job.get("kind", "share"), job.get("title", ""),
                                    job.get("category", ""), job.get("channel", ""),
                                    str(e), retry_info="💡 该链接无法被 115 处理，不会自动重试")
                consecutive_fails = 0  # 任务自身问题不计入熔断
            except Exception as e:
                consecutive_fails += 1
                _fail(job, str(e))
                if consecutive_fails >= 5:
                    logstream.error("transfer", "连续失败 5 次，暂停转存 30 分钟")
                    await asyncio.sleep(1800)
                    consecutive_fails = 0

            await asyncio.sleep(_interval())
        except Exception as e:
            logstream.error("transfer", f"工作器异常: {e}")
            await asyncio.sleep(30)
