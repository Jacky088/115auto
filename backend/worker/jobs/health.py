"""系统健康巡检与周期清理：115 Cookie/TG 健康检测、异常熔断停机、日志与过期记录清理。"""
import asyncio
import time

from ... import database as db
from ...core import logstream, transfer_gate
from ...pan115 import client as pan

def _jobs():
    """延迟获取 jobs 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import jobs
    return jobs


LOG_RETENTION_SECONDS = 7 * 86400  # 日志保留 7 天，到期永久删除

def stop_system(reason: str):
    """运行期出现致命故障：自动停止系统，写实时日志，并给用户设定的 TG 推送警示。联动关闭自动归档。"""
    db.set_setting("running", False)
    db.set_setting("auto_stop_reason", reason)  # 供前端仪表盘展示，用户修正后清除
    db.set_setting("auto_move_enabled", False)  # 顶部停止时，自动归档功能联动关闭
    transfer_gate.request_cancel()
    logstream.error("system", f"⛔ 系统已自动停止：{reason}（自动归档功能已联动关闭）")
    logstream.warn("system", "请按上述原因修正后，点击网页顶栏「开始运行」恢复"
                             "（队列与配置已保留，任务不会丢失）。")
    try:
        _jobs().notify("⛔ 115 自动转存助手已自动停止\n\n"
               f"❓ 原因：{reason}\n\n"
               "🔧 请排查处理后，点击网页顶栏「开始运行」或发送 /start 恢复。")
    except Exception:
        pass  # 通知失败不影响停止动作本身


def _tg_health_ok() -> bool:
    """TG 侧健康检查：
    - 纯 Userbot 频道（无轮询兜底）时 Userbot 必须在线；
    - 存在启用中的轮询频道时，轮询失败率需低于阈值（如频道被删、t.me 被墙则视为失效）。
    """
    rows = db.query("SELECT id, type, enabled FROM channels")
    enabled = [dict(r) for r in rows if r["enabled"]]
    polling = [r for r in enabled if r["type"] == "polling"]
    userbots = [r for r in enabled if r["type"] == "userbot"]
    if userbots and not polling:
        if not db.get_setting("userbot_ok", False):
            return False
    if polling and db.get_setting("running", False):
        # 轮询失败次数由 tg/polling.py 记录：全部启用频道连续 5 轮失败视为 TG 通道失效
        return not _polling_all_failed(list(polling))
    return True


def _polling_all_failed(chans: list[dict]) -> bool:
    """所有启用中的轮询频道都处于连续失败状态（失败轮数达阈值）。"""
    for ch in chans:
        try:
            fails = int(db.get_setting(f"poll_fail_{ch['id']}", 0) or 0)
        except (TypeError, ValueError):
            fails = 0
        if fails < POLL_FAIL_THRESHOLD:
            return False
    return True


POLL_FAIL_THRESHOLD = 5  # 连续失败 5 轮（按最小间隔 30s 计约 2.5 分钟）视为通道失效


def _running_timeout_secs() -> int:
    """看门狗阈值（分钟）：默认 60 分钟，限制在 15-720 之间，避免误配。"""
    try:
        minutes = int(db.get_setting("running_timeout_min", 60) or 60)
    except (TypeError, ValueError):
        minutes = 60
    return max(15, min(720, minutes)) * 60


async def health_check_loop():
    """运行期健康监控：每 60 秒检查工作器存活心跳与转存超时；按配置周期检查 115 与 TG 状态。

    - 仅在 running=True 时生效（未运行只做静默记录，不打扰）
    - 连续 2 次（约 4 分钟）确认失败才终止：避免单次网络抖动误杀
    - 恢复后自动清除故障标记
    """
    logstream.info("system", "运行健康监控任务启动（按配置周期检查 115 与 TG 状态，持续巡检工作器心跳）")
    fail_streak: dict[str, int] = {"115": 0, "tg": 0}
    last_external_check = 0.0
    while True:
        await asyncio.sleep(60)
        try:
            if not await asyncio.to_thread(db.get_setting, "running", False):
                fail_streak = {"115": 0, "tg": 0}
                continue

            # --- 看门狗 1：转存工作器协程健康检测与自愈（防底层网络阻塞导致队列停摆）---
            try:
                from .. import queue_worker
                wt = queue_worker.get_worker_task()
                if wt is None or wt.done():
                    logstream.error("transfer", "看门狗：检测到转存工作器协程已停止运行，正在自动重启自愈...")
                    queue_worker.restart_worker_task()
                elif queue_worker.is_worker_stalled(600.0):
                    logstream.error("transfer", "看门狗：转存工作器超过 10 分钟无心跳响应（疑似协程假死），正在重启自愈...")
                    queue_worker.restart_worker_task()
            except Exception as e:
                logstream.warn("system", f"工作器健康检查异常: {e}")

            # --- 看门狗 2：转存任务卡在 running 超过阈值（115 请求无响应）→ 自动重新排队 ---
            timeout_secs = await asyncio.to_thread(_running_timeout_secs)
            requeued = await asyncio.to_thread(
                db.execute,
                "UPDATE transfers SET status='queued', attempts=attempts+1, running_at=NULL,"
                " error='转存超时：115 请求长时间无响应，已自动重新排队'"
                " WHERE status='running' AND running_at IS NOT NULL AND running_at < ?",
                (time.time() - timeout_secs,))
            if requeued:
                logstream.warn("transfer", f"看门狗：{requeued} 个任务转存超时"
                                           f"（>{timeout_secs // 60} 分钟无心跳），已重新排队"
                                           "（重排计入重试次数，新线程将先做防重预检）")

            # --- 外部依赖周期检测（115 Cookie / TG 状态，按 health_check_interval_min 周期执行）---
            now_ts = time.time()
            interval_min = max(1, int(float(await asyncio.to_thread(
                db.get_setting, "health_check_interval_min", 10) or 10)))
            if now_ts - last_external_check < interval_min * 60:
                continue
            last_external_check = now_ts

            # --- 115 Cookie ---
            # 正在执行转存或文件整理时优先复用缓存，避免向繁忙中的 115 并发强刷校验请求
            in_heavy_115 = (
                transfer_gate.get_gate_state() == "organizing"
                or transfer_gate.is_transfer_active()
            )
            st = await pan.run_pan_thread(pan.check_login, not in_heavy_115)
            if st.get("ok") is None:
                # 网络抖动等未知状态：只告警，不计入 Cookie 失效熔断（避免误停机）
                logstream.warn("115", f"115 Cookie 检查结果未知（网络异常，Cookie 状态保持不变）: "
                                      f"{st.get('err', '')[:200]}")
            elif st.get("ok"):
                if fail_streak["115"]:
                    logstream.info("115", "115 Cookie 已恢复有效")
                    await asyncio.to_thread(db.set_setting, "cookie_prewarn_at", 0)
                fail_streak["115"] = 0
            else:
                fail_streak["115"] += 1
                logstream.warn("115", f"115 Cookie 检查失败（连续 {fail_streak['115']} 次）: "
                                      f"{st.get('err', '')[:200]}")
                if fail_streak["115"] == 1:
                    last_pw = float(await asyncio.to_thread(db.get_setting, "cookie_prewarn_at", 0) or 0)
                    if time.time() - last_pw >= 21600:
                        await asyncio.to_thread(db.set_setting, "cookie_prewarn_at", time.time())
                        await pan.run_pan_thread(
                            _jobs().notify,
                            "⚠️ 115 Cookie 异常预警（首次检测失败）\n\n"
                            f"详情：{st.get('err', '')[:120]}\n"
                            "当前暂未触发自动停机；若下次巡检仍失败将自动停机保护，建议提前检查或更新 Cookie。",
                        )
                if fail_streak["115"] >= 2:
                    # stop_system 内含同步 TG 通知（httpx 最长 15s）：放线程池，避免阻塞事件循环
                    await pan.run_pan_thread(
                        stop_system,
                        f"115 Cookie 失效（{st.get('err', '')[:120]}），"
                        "转存已无法进行。请到「账号管理」重新扫码登录或导入 Cookie。")
                    fail_streak = {"115": 0, "tg": 0}
                    continue

            # --- TG 状态 ---
            if await asyncio.to_thread(_tg_health_ok):
                if fail_streak["tg"]:
                    logstream.info("system", "TG 通道已恢复正常")
                fail_streak["tg"] = 0
            else:
                fail_streak["tg"] += 1
                logstream.warn("system", f"TG 通道异常（连续 {fail_streak['tg']} 次），"
                                         "无法接收频道消息")
                if fail_streak["tg"] >= 2:
                    # 区分两类 TG 故障给出对应修正指引
                    rows = await asyncio.to_thread(db.query, "SELECT id, type, enabled FROM channels")
                    enabled = [dict(r) for r in rows if r["enabled"]]
                    polling = [r for r in enabled if r["type"] == "polling"]
                    stop_reason = ("TG 频道抓取持续失败（频道可能已被删除或转为私密，"
                                   "或服务器无法访问 t.me）。请到「账号管理」的「消息预览」核对，"
                                   "或检查设置页的 TG 代理。") if polling and _polling_all_failed(polling) \
                        else ("TG Userbot 已断开，监控的私密频道消息无法接收。"
                              "请到「账号管理」重新登录 Userbot。")
                    await pan.run_pan_thread(stop_system, stop_reason)
                    fail_streak = {"115": 0, "tg": 0}
        except Exception as e:
            logstream.error("system", f"健康监控异常（本轮跳过）: {logstream.exc(e)}")


MSG_RETENTION_SECONDS = 30 * 86400   # 消息去重记录保留 30 天（防重复转存的依据，需长于日志）
HITS_RETENTION_SECONDS = 90 * 86400  # 黑名单命中记录保留 90 天（低频数据，留足复核期）
DUP_RETENTION_SECONDS = 7 * 86400    # 重复/跳过任务记录保留 7 天（失败的正片内容成功/失败台账长期保留）
MEDIA_TASK_RETENTION_SECONDS = 30 * 86400  # 已完成/失败/忽略的整理刮削记录保留 30 天；待确认/已跳过保留供随时提交


def _cleanup_once():
    """执行一轮过期数据清理，返回各类删除条数。

    分批删除（每批 2000 条）：单事务删几十万行会长时间持有 SQLite 写锁，
    把整个应用（页面接口、WS 心跳、日志写入）全部卡住；分批把每次持锁时间压到毫秒级。
    """
    total = [0, 0, 0, 0, 0, 0, 0]
    # messages 表是 (channel, message_id) 复合主键没有 id 列，统一用 SQLite 隐式 rowid
    from datetime import date, timedelta
    cutoff_day = (date.today() - timedelta(days=30)).isoformat()
    jobs = (
        ("DELETE FROM logs WHERE rowid IN (SELECT rowid FROM logs WHERE created_at<? LIMIT 2000)",
         time.time() - LOG_RETENTION_SECONDS, 0),
        ("DELETE FROM messages WHERE rowid IN (SELECT rowid FROM messages WHERE processed_at<? LIMIT 2000)",
         time.time() - MSG_RETENTION_SECONDS, 1),
        ("DELETE FROM blacklist_hits WHERE rowid IN (SELECT rowid FROM blacklist_hits WHERE created_at<? LIMIT 2000)",
         time.time() - HITS_RETENTION_SECONDS, 2),
        ("DELETE FROM daily_quota WHERE day < ?",
         cutoff_day, 3),
        # 同一分享被反复命中会持续写入 duplicate/skipped 记录：只清理这两类，
        # success/failed 是用户核心台账，继续永久保留
        ("DELETE FROM transfers WHERE rowid IN (SELECT rowid FROM transfers WHERE status='duplicate'"
         " AND created_at<? LIMIT 2000)",
         time.time() - DUP_RETENTION_SECONDS, 4),
        ("DELETE FROM transfers WHERE rowid IN (SELECT rowid FROM transfers WHERE status='skipped'"
         " AND created_at<? LIMIT 2000)",
         time.time() - DUP_RETENTION_SECONDS, 5),
        ("DELETE FROM media_tasks WHERE rowid IN (SELECT rowid FROM media_tasks"
         " WHERE status IN ('done','failed','ignored')"
         " AND COALESCE(finished_at, updated_at, created_at) < ? LIMIT 2000)",
         time.time() - MEDIA_TASK_RETENTION_SECONDS, 6),
    )
    for sql, cutoff, idx in jobs:
        while True:
            n = db.execute(sql, (cutoff,))
            total[idx] += n
            if n < 2000:
                break
    return tuple(total)


async def log_cleanup_loop():
    """每小时清理一次过期数据，防止数据库随时间无限膨胀：
    - 日志：保留 7 天（网页可查/导出）
    - 消息去重记录：保留 30 天（超过后该消息若再出现会被重新处理——30 天前的资源重转风险可忽略）
    - 黑名单命中记录：保留 90 天（低频写入，留足人工复核期）
    - 每日额度记录：保留 30 天
    - 重复/跳过任务：保留 7 天（success/failed 台账永久保留）
    - 已结束的整理刮削记录（done/failed/ignored）：保留 30 天（待确认/已跳过永久保留）
    """
    logstream.info("system", "数据自动清理任务启动（日志 7 天 / 消息 30 天 / 命中记录 90 天 / 重复与跳过 7 天 / 已结束刮削 30 天）")
    # 延迟首轮清理，避免刚启动时与初始化/短生命周期请求争用写锁
    await asyncio.sleep(60)
    while True:
        try:
            # 清理是批量 DB 写操作：放线程池执行，不阻塞事件循环
            deleted, stale_msgs, stale_hits, stale_quota, stale_dup, stale_skip, stale_media = \
                await asyncio.to_thread(_cleanup_once)
            if deleted:
                logstream.info("system", f"已清理 {deleted} 条过期日志（超过 7 天）")
            if stale_msgs:
                logstream.info("system", f"已清理 {stale_msgs} 条过期消息去重记录（超过 30 天）")
            if stale_hits:
                logstream.info("system", f"已清理 {stale_hits} 条过期黑名单命中记录（超过 90 天）")
            if stale_quota:
                logstream.info("system", f"已清理 {stale_quota} 条过期每日额度记录（超过 30 天）")
            if stale_dup or stale_skip:
                logstream.info("system", f"已清理 {stale_dup + stale_skip} 条过期重复/跳过任务记录（超过 7 天）")
            if stale_media:
                logstream.info("system", f"已清理 {stale_media} 条已结束的整理刮削历史（超过 30 天）")
        except Exception as e:
            logstream.error("system", f"数据清理失败: {logstream.exc(e)}")
        await asyncio.sleep(3600)
