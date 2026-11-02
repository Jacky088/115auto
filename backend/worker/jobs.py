"""定时巡检：115 cookie 有效性（10 分钟一次）+ 日志 7 天自动清理 + TG 通知。"""
import asyncio
import re
import time

import httpx

from .. import database as db
from ..core import logstream
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


async def cookie_check_loop():
    last_state = None
    while True:
        try:
            # 每 10 分钟真实校验一次（force 跳过缓存）：既监控失效，也能发现恢复
            st = await asyncio.to_thread(pan.check_login, True)
            ok = bool(st.get("ok"))
            if not ok and last_state is True:
                logstream.error("115", f"115 cookie 失效，请到网页重新扫码！({st.get('err', '')})")
                await asyncio.to_thread(notify, "⚠️ 115 cookie 已失效，请打开管理页面重新扫码登录")
            elif ok and last_state is False:
                logstream.info("115", "115 cookie 已恢复有效")
            last_state = ok
        except Exception:
            pass
        await asyncio.sleep(600)


async def log_cleanup_loop():
    """每小时清理一次超过保留期（7 天）的日志，永久删除。"""
    logstream.info("system", "日志自动清理任务启动（保留 7 天）")
    while True:
        try:
            deleted = db.execute("DELETE FROM logs WHERE created_at<?",
                                 (time.time() - LOG_RETENTION_SECONDS,))
            if deleted:
                logstream.info("system", f"已永久删除 {deleted} 条过期日志（超过 7 天）")
        except Exception as e:
            logstream.error("system", f"日志清理失败: {e}")
        await asyncio.sleep(3600)


OFFLINE_CONFIRM_WINDOW = 3 * 86400  # 云下载提交后 3 天内跟踪结果


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
                    u = t.get("url") or ""
                    if u:
                        by_url[u] = t
                for job in subs:
                    t = by_url.get(job["share_url"])
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
            logstream.error("transfer", f"云下载结果核对异常: {e}")
        await asyncio.sleep(600)


def _finish_offline(job: dict, ok: bool, name: str = "", reason: str = ""):
    if ok:
        db.execute("UPDATE transfers SET status='success', title=?, finished_at=? WHERE id=?",
                   ((name or job["title"])[:200], time.time(), job["id"]))
        db.quota_incr()
        logstream.info("transfer",
                       f"✅ 云下载任务完成（②）: {(name or job['title'])[:60]} → {job['target_dir']}，文件已保存至目标目录")
        notify_transfer(name or job["title"], job.get("category", ""), job.get("channel", ""),
                        job["target_dir"], kind="offline_done")
    else:
        db.execute("UPDATE transfers SET status='failed', error=? WHERE id=?",
                   (reason[:500], job["id"]))
        logstream.error("transfer", f"❌ 云下载任务失败: {job['title'][:60]} — {reason}")
        notify_failure("offline", job["title"], job.get("category", ""),
                       job.get("channel", ""), reason,
                       retry_info="💡 该任务已提交 115 云下载但未成功，请检查链接有效性后重新添加")
