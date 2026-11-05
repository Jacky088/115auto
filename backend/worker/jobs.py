"""定时巡检：115 cookie 有效性（10 分钟一次）+ 日志 7 天自动清理 + TG 通知。"""
import asyncio
import re
import time
from urllib.parse import unquote

import httpx

from .. import database as db
from ..core import classifier, logstream
from ..pan115 import client as pan

LOG_RETENTION_SECONDS = 7 * 86400  # 日志保留 7 天，到期永久删除


def _tg_proxy() -> str | None:
    """TG 相关请求（通知/巡检）复用设置页的 TG 代理。"""
    return str(db.get_setting("proxy", "") or "") or None


def notify(message: str) -> bool:
    """通过 TG Bot 发通知，失败静默（不影响主流程）。"""
    token = db.get_setting("notify_token", "")
    chat_id = db.get_setting("notify_chat_id", "")
    if not token or not chat_id:
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": str(chat_id), "text": message, "disable_web_page_preview": True},
            proxy=_tg_proxy(), timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        return False


def test_proxy(proxy: str) -> tuple[bool, str]:
    """测试 TG 代理连通性：通过代理访问 Telegram 官方 API 站点。"""
    proxy = (proxy or "").strip()
    if not proxy:
        return False, "请先填写代理地址（如 socks5://127.0.0.1:1080 或 http://127.0.0.1:7890）"
    try:
        t0 = time.time()
        resp = httpx.get("https://api.telegram.org", proxy=proxy, timeout=12)
        ms = int((time.time() - t0) * 1000)
        if resp.status_code in (200, 301, 302, 404):
            return True, f"代理可用，延迟 {ms}ms"
        return False, f"代理可连通，但返回异常状态 {resp.status_code}"
    except Exception as e:
        return False, (f"无法通过该代理访问 Telegram（{type(e).__name__}）。"
                       "请确认代理服务正在运行、地址端口正确、协议为 socks5:// 或 http://")


def send_test(token: str, chat_id: str) -> tuple[bool, str]:
    """发送测试通知，返回 (是否成功, 失败原因)。给网页测试按钮用，定位具体问题。"""
    token = (token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not token or not chat_id:
        return False, "请先填写 Bot Token 和 Chat ID"
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id,
                  "text": "✅ 115自动转存助手：测试通知成功！\n转存成功后会推送到此对话。",
                  "disable_web_page_preview": True},
            proxy=_tg_proxy(), timeout=15,
        )
        if resp.status_code == 200:
            return True, ""
        try:
            desc = resp.json().get("description", "")
        except Exception:
            desc = ""
        if resp.status_code == 401:
            return False, "Bot Token 无效，请检查是否从 @BotFather 完整复制（含冒号前的数字ID）"
        if "chat not found" in desc.lower():
            return False, "Chat ID 无效（找不到该对话），请先给你的 Bot 发送过一条 /start 消息"
        return False, f"Telegram API 返回 {resp.status_code}：{desc or resp.text[:120]}"
    except Exception as e:
        return False, (f"无法连接 Telegram（{type(e).__name__}），请检查服务器网络；"
                       "大陆服务器请在上方「TG 代理」填写代理地址")


_EP_RE = re.compile(r"[Ss]\d{1,2}\s?[Ee]\d{1,3}|第\s?\d+\s?[集话話期]")


def extract_episode(title: str) -> str:
    """从标题提取集数标识（S01E40 / 第495集 / 第3期），提取不到返回空。"""
    m = _EP_RE.search(title or "")
    return m.group(0).replace(" ", "") if m else ""


def _fmt_size(size: int) -> str:
    if not size:
        return ""
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}" if unit not in ("B",) else f"{int(size)} B"
        size /= 1024
    return f"{size:.2f} TB"


def notify_failure(kind: str, title: str, category: str, source: str, reason: str,
                   retry_info: str = ""):
    """转存/云下载失败通知（含具体原因）。失败静默，不影响主流程。"""
    try:
        if not db.get_setting("notify_transfer_success", True):
            return
        title = classifier.headline(title) or title
        lines = ["❌ 云下载失败" if kind == "offline" else "❌ 转存失败", ""]
        lines.append("📺 " + (title or "未命名")[:80])
        if category:
            lines.append("🏷 分类：" + category)
        if source:
            lines.append("📡 来源：@" + source.lstrip("@"))
        if reason:
            lines.append("❓ 原因：" + reason[:200])
        if retry_info:
            lines.append(retry_info)
        notify("\n".join(lines))
    except Exception:
        pass


def notify_transfer(title: str, category: str, source: str, target: str,
                    size: int = 0, kind: str = "share", file_count: int = 0):
    """转存/云下载结果通知（简洁格式）。kind: share 分享转存成功 | offline 已提交云下载 | offline_done 云下载完成。失败静默，不影响主流程。"""
    try:
        if not db.get_setting("notify_transfer_success", True):
            return
        title = classifier.headline(title) or title
        head = {"offline": "☁️ 云下载任务已添加", "offline_done": "✅ 云下载任务完成"}.get(kind, "✅ 转存成功")
        lines = [head, ""]
        lines.append("📺 " + (title or "未命名")[:80])
        if category:
            lines.append("🏷 分类：" + category)
        ep = extract_episode(title)
        if ep:
            lines.append("🎬 集数：" + ep)
        if source:
            lines.append("📡 来源：@" + source.lstrip("@"))
        if target:
            lines.append("📂 目标：" + target)
        if kind == "offline":
            lines.append("① 任务已提交至 115 云下载")
            lines.append("⏳ 下载完成后将推送第②条通知")
        elif kind == "offline_done":
            lines.append("② 115 已下载完毕，文件已保存至目标目录")
        if size:
            lines.append("💾 大小：" + _fmt_size(size))
        if file_count:
            lines.append("🗃 文件数：" + str(file_count))
        notify("\n".join(lines))
    except Exception:
        pass  # 通知失败不影响转存


def stop_system(reason: str):
    """运行期出现致命故障：自动停止系统，写实时日志，并给用户设定的 TG 推送警示。"""
    db.set_setting("running", False)
    db.set_setting("auto_stop_reason", reason)  # 供前端仪表盘展示，用户修正后清除
    logstream.error("system", f"⛔ 系统已自动停止运行：{reason}")
    logstream.warn("system", "请修正上述问题后，回到网页顶栏点击「开始运行」恢复。"
                             "停止期间队列与配置保留，不会丢失任务。")
    try:
        notify("⛔ 115 自动转存助手已自动停止运行\n\n"
               f"❓ 原因：{reason}\n\n"
               "🔧 请到管理页面修正后，点击顶栏「开始运行」恢复。\n"
               "（停止期间队列与配置保留，任务不会丢失）")
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


async def health_check_loop():
    """运行期健康监控（每 2 分钟）：115 Cookie 与 TG 状态任一失效 → 自动终止运行。

    - 仅在 running=True 时生效（未运行只做静默记录，不打扰）
    - 连续 2 次（约 4 分钟）确认失败才终止：避免单次网络抖动误杀
    - 恢复后自动清除故障标记
    """
    logstream.info("system", "运行健康监控任务启动（每 2 分钟检查 115 与 TG 状态）")
    fail_streak: dict[str, int] = {"115": 0, "tg": 0}
    while True:
        await asyncio.sleep(120)
        try:
            if not db.get_setting("running", False):
                fail_streak = {"115": 0, "tg": 0}
                continue

            # --- 115 Cookie ---
            st = await asyncio.to_thread(pan.check_login, True)
            if st.get("ok"):
                if fail_streak["115"]:
                    logstream.info("115", "115 Cookie 已恢复有效")
                fail_streak["115"] = 0
            else:
                fail_streak["115"] += 1
                logstream.warn("115", f"115 Cookie 检查失败（连续 {fail_streak['115']} 次）: "
                                      f"{st.get('err', '')[:200]}")
                if fail_streak["115"] >= 2:
                    stop_system(f"115 Cookie 失效（{st.get('err', '')[:120]}），"
                                "转存已无法进行。请到「账号管理」重新扫码登录或导入 Cookie。")
                    fail_streak = {"115": 0, "tg": 0}
                    continue

            # --- TG 状态 ---
            if _tg_health_ok():
                if fail_streak["tg"]:
                    logstream.info("system", "TG 通道已恢复正常")
                fail_streak["tg"] = 0
            else:
                fail_streak["tg"] += 1
                logstream.warn("system", f"TG Userbot 未连接（连续 {fail_streak['tg']} 次），"
                                         "私有频道消息无法接收")
                if fail_streak["tg"] >= 2:
                    # 区分两类 TG 故障给出对应修正指引
                    rows = db.query("SELECT id, type, enabled FROM channels")
                    enabled = [dict(r) for r in rows if r["enabled"]]
                    polling = [r for r in enabled if r["type"] == "polling"]
                    if polling and _polling_all_failed(polling):
                        reason = ("TG 频道抓取持续失败（频道可能已被删除/转为私有，"
                                  "或服务器无法访问 t.me）。请到「频道管理」用「消息预览」核对频道，"
                                  "或检查设置页的 TG 代理。")
                    else:
                        reason = ("TG Userbot 已断开，监控的私有频道消息无法接收。"
                                  "请到「账号管理」重新登录 Userbot。")
                    stop_system(reason)
                    fail_streak = {"115": 0, "tg": 0}
        except Exception as e:
            logstream.error("system", f"健康监控异常（本轮跳过）: {logstream.exc(e)}")


MSG_RETENTION_SECONDS = 30 * 86400   # 消息去重记录保留 30 天（防重复转存的依据，需长于日志）
HITS_RETENTION_SECONDS = 90 * 86400  # 黑名单命中记录保留 90 天（低频数据，留足复核期）


async def log_cleanup_loop():
    """每小时清理一次过期数据，防止数据库随时间无限膨胀：
    - 日志：保留 7 天（网页可查/导出）
    - 消息去重记录：保留 30 天（超过后该消息若再出现会被重新处理——30 天前的资源重转风险可忽略）
    - 黑名单命中记录：保留 90 天（低频写入，留足人工复核期）
    转存记录永久保留：体积极小（无正文），且是用户的核心操作台账。
    """
    logstream.info("system", "数据自动清理任务启动（日志 7 天 / 消息 30 天 / 命中记录 90 天）")
    while True:
        try:
            deleted = db.execute("DELETE FROM logs WHERE created_at<?",
                                 (time.time() - LOG_RETENTION_SECONDS,))
            if deleted:
                logstream.info("system", f"已永久删除 {deleted} 条过期日志（超过 7 天）")
            stale_msgs = db.execute("DELETE FROM messages WHERE processed_at<?",
                                    (time.time() - MSG_RETENTION_SECONDS,))
            if stale_msgs:
                logstream.info("system", f"已清理 {stale_msgs} 条过期消息去重记录（超过 30 天）")
            stale_hits = db.execute("DELETE FROM blacklist_hits WHERE created_at<?",
                                    (time.time() - HITS_RETENTION_SECONDS,))
            if stale_hits:
                logstream.info("system", f"已归档删除 {stale_hits} 条过期黑名单命中记录（超过 90 天）")
        except Exception as e:
            logstream.error("system", f"数据清理失败: {logstream.exc(e)}")
        await asyncio.sleep(3600)


OFFLINE_CONFIRM_WINDOW = 3 * 86400  # 云下载提交后 3 天内跟踪结果


def _offline_key(url: str) -> str:
    """云下载 URL 归一化，用于和 115 任务列表里的 url 匹配。

    115 任务列表可能把 URL 做 percent-解码/编码、改大小写或去掉结尾斜杠，
    ED2K/磁力这类带中文文件名的链接（如「厨房春光...iso」）直接字符串比对会匹配失败，
    任务因此一直找不到、超窗后被误判为下载失败。ED2K/磁力统一按 hash 匹配，其余按小写。
    """
    u = unquote((url or "").strip())
    if not u:
        return ""
    if u.lower().startswith("ed2k://"):
        parts = u.split("|")
        if len(parts) >= 5 and parts[4]:
            return "ed2k:" + parts[4].lower()
    m = re.search(r"xt=urn:btih:([0-9A-Za-z]+)", u, re.I)
    if m:
        return "btih:" + m.group(1).lower()
    return u.rstrip("/").lower()


async def offline_check_loop():
    """每 10 分钟核对"已提交"云下载任务的真实结果（下载完成/失败），防止误报成功。

    115 任务状态：percent>=100、status 10-11、status>=13 为完成；status 1-9 为失败；
    status 12 为下载中。
    """
    logstream.info("transfer", "云下载结果跟踪任务启动（每 10 分钟核对一次）")
    while True:
        try:
            # 扫描全部「已提交」任务（不只是 3 天内）：超窗且列表里找不到的会在此被判失败，
            # 避免任务永远卡在 submitted
            subs = db.query("SELECT * FROM transfers WHERE status='submitted'")
            if subs:
                try:
                    tasks = await asyncio.to_thread(pan.task_list_all)
                except Exception as e:
                    # 接口异常时绝不能按「列表中找不到」判失败，本轮直接跳过
                    logstream.warn("transfer", f"拉取 115 云下载任务列表失败，本轮跳过核对: {e}")
                    await asyncio.sleep(600)
                    continue
                by_url = {}
                for t in tasks:
                    key = _offline_key(t.get("url") or "")
                    if key:
                        by_url[key] = t
                for job in subs:
                    t = by_url.get(_offline_key(job["share_url"]))
                    if t is None:
                        # 列表里找不到：超窗后按失败处理，否则继续等
                        if time.time() - job["created_at"] > OFFLINE_CONFIRM_WINDOW:
                            await asyncio.to_thread(
                                _finish_offline, job, False, "",
                                "提交后 3 天内未在 115 任务列表中找到该任务")
                        continue
                    try:
                        percent = float(t.get("percent", 0) or 0)
                    except (TypeError, ValueError):
                        percent = 0.0
                    try:
                        status = int(t.get("status", 0) or 0)
                    except (TypeError, ValueError):
                        status = 0
                    if percent >= 100 or status in (10, 11) or status >= 13:
                        await asyncio.to_thread(_finish_offline, job, True, t.get("name", ""), "")
                    elif 1 <= status <= 9:
                        await asyncio.to_thread(
                            _finish_offline, job, False, "", "115 下载任务失败（链接无效或无资源）")
                    # 其余状态 = 下载中，继续等待
        except Exception as e:
            logstream.error("transfer", f"云下载结果核对异常: {logstream.exc(e)}")
        await asyncio.sleep(600)


def _finish_offline(job: dict, ok: bool, name: str = "", reason: str = ""):
    if ok:
        # 名称优先取频道消息标题，115 返回的文件名仅兜底
        shown = classifier.headline(job.get("title", "")) or name or job["title"]
        db.execute("UPDATE transfers SET status='success', title=?, finished_at=? WHERE id=?",
                   (shown[:200], time.time(), job["id"]))
        db.quota_incr()
        logstream.info("transfer",
                       f"✅ 云下载任务完成（②）: {shown[:60]} → {job['target_dir']}，文件已保存至目标目录")
        notify_transfer(shown, job.get("category", ""), job.get("channel", ""),
                        job["target_dir"], kind="offline_done")
    else:
        db.execute("UPDATE transfers SET status='failed', error=? WHERE id=?",
                   (reason[:500], job["id"]))
        logstream.error("transfer", f"❌ 云下载任务失败: {job['title'][:60]} — {reason}")
        notify_failure("offline", job["title"], job.get("category", ""),
                       job.get("channel", ""), reason,
                       retry_info="💡 该任务已提交 115 云下载但未成功，请检查链接有效性后重新添加")
