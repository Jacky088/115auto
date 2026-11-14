"""定时巡检：115 cookie 有效性（10 分钟一次）+ 日志 7 天自动清理 + TG 通知。"""
import asyncio
import datetime
import json
import os
import queue
import re
import threading
import time
import uuid
from urllib.parse import unquote

import httpx

from .. import database as db
from ..core import classifier, logstream, transfer_gate
from ..pan115 import client as pan

LOG_RETENTION_SECONDS = 7 * 86400  # 日志保留 7 天，到期永久删除

# ---------- Telegram 消息推送排队系统 ----------
# 线程安全的 FIFO 队列，防止多任务并发或定时任务集中触发 TG 429 频控或消息丢失
_tg_notify_queue: queue.Queue = queue.Queue(maxsize=1000)


def enqueue_tg_message(
    text: str,
    reply_markup: dict | None = None,
    on_sent=None,
    gap_sec: float | None = None,
) -> bool:
    """将一条文本通知加入 TG 发送队列（可选内联键盘、发送后回调与自定义节流间隔）。"""
    token = str(db.get_setting("notify_token", "") or "").strip()
    chat_id = str(db.get_setting("notify_chat_id", "") or "").strip()
    if not token or not chat_id:
        return False
    try:
        item = {
            "type": "text",
            "token": token,
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup is not None:
            item["reply_markup"] = reply_markup
        if on_sent is not None:
            item["on_sent"] = on_sent
        if gap_sec is not None:
            item["gap_sec"] = float(gap_sec)
        _tg_notify_queue.put_nowait(item)
        return True
    except queue.Full:
        logstream.warn("system", "TG 推送队列已满（1000 条），放弃排队新消息")
        return False


def enqueue_tg_edit(
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
    chat_id: str = "",
    gap_sec: float = 0.6,
) -> bool:
    """将一次原位卡片编辑（editMessageText）纳入严格串行队列，避免与推卡竞态或触发 TG 限流。"""
    token = str(db.get_setting("notify_token", "") or "").strip()
    target_chat = str(chat_id or db.get_setting("notify_chat_id", "") or "").strip()
    if not token or not target_chat or int(message_id or 0) <= 0:
        return False
    try:
        _tg_notify_queue.put_nowait({
            "type": "edit",
            "token": token,
            "chat_id": target_chat,
            "message_id": int(message_id),
            "text": text,
            "reply_markup": reply_markup,
            "gap_sec": float(gap_sec),
        })
        return True
    except queue.Full:
        return False


def enqueue_tg_document(fname: str, content: bytes, mime: str, caption: str) -> bool:
    """将一份文档（如定时备份数据）加入 TG 发送队列。"""
    token = str(db.get_setting("notify_token", "") or "").strip()
    chat_id = str(db.get_setting("notify_chat_id", "") or "").strip()
    if not token or not chat_id:
        return False
    try:
        _tg_notify_queue.put_nowait({
            "type": "document",
            "token": token,
            "chat_id": chat_id,
            "fname": fname,
            "content": content,
            "mime": mime,
            "caption": caption,
        })
        return True
    except queue.Full:
        logstream.warn("system", "TG 推送队列已满，放弃排队文档")
        return False


async def tg_notify_worker():
    """TG 通知消费工作协程：单流排队平滑发送、频控退避（429 智能重试）、网络故障重试。"""
    logstream.info("system", "TG 通知排队消费者已就绪")
    while True:
        try:
            try:
                item = _tg_notify_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.5)
                continue

            item_type = item.get("type", "text")
            token = item["token"]
            chat_id = item["chat_id"]
            proxy = _tg_proxy()

            retries = 0
            max_retries = 3
            success = False

            while retries < max_retries and not success:
                try:
                    if item_type == "text":
                        payload = {
                            "chat_id": str(chat_id),
                            "text": item["text"],
                            "disable_web_page_preview": True,
                        }
                        if item.get("reply_markup") is not None:
                            payload["reply_markup"] = item["reply_markup"]
                        async with httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
                            resp = await client.post(
                                f"https://api.telegram.org/bot{token}/sendMessage",
                                json=payload,
                            )
                    elif item_type == "edit":
                        payload = {
                            "chat_id": str(chat_id),
                            "message_id": int(item["message_id"]),
                            "text": item["text"],
                            "disable_web_page_preview": True,
                            "reply_markup": item.get("reply_markup") or {"inline_keyboard": []},
                        }
                        async with httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
                            resp = await client.post(
                                f"https://api.telegram.org/bot{token}/editMessageText",
                                json=payload,
                            )
                        if resp.status_code == 400 and (
                            "message is not modified" in resp.text
                            or "message to edit not found" in resp.text
                        ):
                            success = True
                            break
                    else:
                        async with httpx.AsyncClient(proxy=proxy, timeout=60.0) as client:
                            resp = await client.post(
                                f"https://api.telegram.org/bot{token}/sendDocument",
                                data={"chat_id": str(chat_id), "caption": item["caption"]},
                                files={"document": (item["fname"], item["content"], item["mime"])},
                            )

                    if resp.status_code == 200:
                        cb = item.get("on_sent")
                        if callable(cb):
                            try:
                                msg_id = int((resp.json().get("result") or {}).get("message_id") or 0)
                                if msg_id > 0:
                                    await asyncio.to_thread(cb, msg_id, str(chat_id))
                            except Exception as cbe:
                                logstream.warn("system", f"TG 发送后回调执行异常: {cbe}")
                        success = True
                        break
                    elif resp.status_code == 429:
                        try:
                            retry_after = int(resp.json().get("parameters", {}).get("retry_after", 3))
                        except Exception:
                            retry_after = 5
                        retry_after = max(1, min(60, retry_after))
                        logstream.warn("system", f"TG 推送触发 429 限流，等待 {retry_after} 秒后自动接续重试...")
                        await asyncio.sleep(retry_after)
                        continue
                    else:
                        retries += 1
                        desc = resp.text[:120]
                        if retries < max_retries:
                            await asyncio.sleep(2 * retries)
                        else:
                            logstream.warn("system", f"TG 通知推送失败（HTTP {resp.status_code}）：{desc}")
                except Exception as e:
                    retries += 1
                    if retries < max_retries:
                        await asyncio.sleep(2 * retries)
                    else:
                        logstream.warn("system", f"TG 通知推送网络异常：{type(e).__name__} {e}")

            _tg_notify_queue.task_done()
            # 严格限速保护：TG 单聊限额为 1 条/秒，故每条消息发送完毕后至少等待 1.0 秒（文件 1.5 秒）
            delay = item.get("gap_sec")
            if delay is None:
                delay = 1.5 if item_type == "document" else 1.0
            await asyncio.sleep(max(0.4, float(delay)))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logstream.error("system", f"TG 通知排队消费异常: {logstream.exc(e)}")
            await asyncio.sleep(1.0)


def _tg_proxy() -> str | None:
    """TG 相关请求（通知/巡检）复用设置页的 TG 代理。"""
    return str(db.get_setting("proxy", "") or "") or None


def _notify_tg(message: str) -> bool:
    """TG Bot 通知：通过 FIFO 队列排队平滑消费，彻底杜绝瞬间并发 429 限制与漏发。"""
    return enqueue_tg_message(message)


def _notify_bark(message: str) -> bool:
    """Bark（iOS）推送。bark_url 形如 https://api.day.app/你的key（支持自建域名）。

    GET {base}/{content}?group=115：group 参数让通知在 Bark App 里归入「115」分组，
    便于与其他 App 推送区分。失败静默。
    """
    base = str(db.get_setting("bark_url", "") or "").strip().rstrip("/")
    if not base:
        return False
    try:
        from urllib.parse import quote
        resp = httpx.get(f"{base}/{quote(message[:400])}",
                         params={"group": "115"}, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def _notify_pushplus(message: str) -> bool:
    """PushPlus（微信推送）。pushplus_token 必填；pushplus_topic 可选（一对多群发）。

    留空 topic 时发给扫码关注的本人；填写群组编码后发给该群全部关注者。
    失败静默。
    """
    token = str(db.get_setting("pushplus_token", "") or "").strip()
    if not token:
        return False
    payload = {"token": token, "title": message.splitlines()[0][:60],
               "content": message, "template": "txt"}
    topic = str(db.get_setting("pushplus_topic", "") or "").strip()
    if topic:
        payload["topic"] = topic
    try:
        resp = httpx.post("https://www.pushplus.plus/send", json=payload, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def _notify_ntfy(message: str) -> bool:
    """ntfy Webhook 推送。ntfy_url 为完整主题 URL（官方或自建均可）。

    ntfy_token 可选：自建 ntfy 开启访问控制时填「密码/token」，
    经 Authorization: Bearer 传递（官方 ntfy.sh 若主题带 -token 后缀也可）。
    失败静默。
    """
    url = str(db.get_setting("ntfy_url", "") or "").strip()
    if not url:
        return False
    headers = {"Title": "115 自动转存", "Tags": "floppy_disk"}
    tok = str(db.get_setting("ntfy_token", "") or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        resp = httpx.post(url, content=message.encode()[:4000],
                          headers=headers, timeout=10)
        return resp.status_code in (200, 202)
    except Exception:
        return False


def notify(message: str) -> bool:
    """向所有已配置的通知渠道推送，任一成功即返回 True。失败静默，不影响主流程。"""
    return any(notify_detail(message).values())


# 各渠道单发函数：test_channel 与 notify_detail 共用
CHANNEL_FNS = {
    "tg": _notify_tg,
    "bark": _notify_bark,
    "pushplus": _notify_pushplus,
    "ntfy": _notify_ntfy,
}

# 渠道未配置时的提示文案（前端测试按钮直接展示，帮助用户补配置）
CHANNEL_EMPTY_HINT = {
    "tg": "未配置 TG Bot Token / Chat ID（「通知」区）",
    "bark": "未填写 Bark 推送 URL（形如 https://api.day.app/你的key）",
    "pushplus": "未填写 PushPlus Token",
    "ntfy": "未填写 ntfy 主题 URL（形如 https://ntfy.sh/你的主题）",
}


def notify_detail(message: str) -> dict[str, bool]:
    """向所有已配置渠道推送，返回每个渠道的成功情况（用于日志与摘要重试判断）。

    各渠道并发发送并设 20 秒总超时：串行时 4 渠道 × 15 秒会长时间占用调用线程
   （部分调用发生在事件循环/队列循环中，会拖慢转存消费）。
    """
    out: dict[str, bool] = {}
    lock = threading.Lock()

    def _run(ch: str, fn) -> None:
        try:
            ok = bool(fn(message))
        except Exception:
            ok = False
        with lock:
            out[ch] = ok

    threads = [threading.Thread(target=_run, args=(ch, fn), daemon=True)
               for ch, fn in CHANNEL_FNS.items()]
    for t in threads:
        t.start()
    deadline = time.time() + 20
    for t in threads:
        t.join(max(0.0, deadline - time.time()))
    # 未在总超时内返回的渠道按失败记，避免个别渠道拖住整体
    return {ch: bool(out.get(ch, False)) for ch in CHANNEL_FNS}


def test_channel(channel: str) -> tuple[bool, str]:
    """向指定渠道发送一条测试消息，返回 (是否成功, 说明)。供设置页「测试」按钮调用。"""
    fn = CHANNEL_FNS.get(channel)
    if fn is None:
        return False, f"未知通知渠道: {channel}"
    try:
        if fn("🔔 115 自动转存测试通知\n收到这条消息说明该通知渠道配置正确。"):
            return True, "测试消息已发送，请到对应 App 查看"
        # 失败：区分「未配置」和「配置了但发送失败」
        return False, CHANNEL_EMPTY_HINT.get(channel, "发送失败：请检查该渠道配置（URL/Token 是否完整、服务器网络是否可达）")
    except Exception as e:
        return False, f"发送异常：{e}"


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
                  "text": "🔔 115 自动转存助手测试通知\n收到这条消息说明该通知渠道配置正确。",
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
            return False, "Bot Token 无效，请检查是否从 @BotFather 完整复制（含冒号前的数字 ID）"
        if "chat not found" in desc.lower():
            return False, "Chat ID 无效（找不到该对话），请先给你的 Bot 发送一条 /start 消息"
        return False, f"Telegram API 返回 {resp.status_code}：{desc or resp.text[:120]}"
    except Exception as e:
        return False, (f"无法连接 Telegram（{type(e).__name__}），请检查服务器网络；"
                       "国内服务器请在「TG 代理」处填写代理地址")


def extract_episode(title: str) -> str:
    """从标题提取并规范化集数标识（S01E40 / 第495集 / 第3期），提取不到返回空。

    解析口径与订阅追更、向前补全共用 backend/core/episode.py：
    S01E05 / s1e5 / EP5 / 第5集 / 5集 会归一到同一种写法。
    """
    from ..core import episode

    return episode.display(title)


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
    """运行期出现致命故障：自动停止系统，写实时日志，并给用户设定的 TG 推送警示。联动关闭自动归档。"""
    db.set_setting("running", False)
    db.set_setting("auto_stop_reason", reason)  # 供前端仪表盘展示，用户修正后清除
    db.set_setting("auto_move_enabled", False)  # 顶部停止时，自动归档功能联动关闭
    transfer_gate.request_cancel()
    logstream.error("system", f"⛔ 系统已自动停止：{reason}（自动归档功能已联动关闭）")
    logstream.warn("system", "请按上述原因修正后，点击网页顶栏「开始运行」恢复"
                             "（队列与配置已保留，任务不会丢失）。")
    try:
        notify("⛔ 115 自动转存助手已自动停止\n\n"
               f"❓ 原因：{reason}\n\n"
               "🔧 请按上方原因中的指引修正后，点击网页顶栏「开始运行」恢复。")
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
    """运行期健康监控（每 2 分钟）：115 Cookie 与 TG 状态任一失效 → 自动终止运行。

    - 仅在 running=True 时生效（未运行只做静默记录，不打扰）
    - 连续 2 次（约 4 分钟）确认失败才终止：避免单次网络抖动误杀
    - 恢复后自动清除故障标记
    """
    logstream.info("system", "运行健康监控任务启动（按配置周期检查 115 与 TG 状态）")
    fail_streak: dict[str, int] = {"115": 0, "tg": 0}
    while True:
        interval_min = max(1, int(float(await asyncio.to_thread(
            db.get_setting, "health_check_interval_min", 10) or 10)))
        await asyncio.sleep(interval_min * 60)
        try:
            if not await asyncio.to_thread(db.get_setting, "running", False):
                fail_streak = {"115": 0, "tg": 0}
                continue

            # --- 看门狗：转存任务卡在 running 超过阈值（115 请求无响应，
            # p115client 的 HTTP 栈无默认超时）→ 自动重新排队，避免队列永久停摆。
            # 执行中的任务每 60 秒心跳刷新 running_at；真正卡死才会超过阈值。
            # 旧线程若迟到返回，状态守卫（AND status='running'）会拒绝其回写，不会重复计额。
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
                fail_streak["115"] = 0
            else:
                fail_streak["115"] += 1
                logstream.warn("115", f"115 Cookie 检查失败（连续 {fail_streak['115']} 次）: "
                                      f"{st.get('err', '')[:200]}")
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


OFFLINE_CONFIRM_WINDOW = 3 * 86400  # 云下载提交后 3 天内跟踪结果


def _extract_btih(url: str) -> str:
    """从磁力链提取 40 位 hex hash，支持 base32 与 hex。"""
    u = unquote((url or "").strip())
    m = re.search(r"xt=urn:btih:([0-9A-Za-z]+)", u, re.I)
    if m:
        val = m.group(1).lower()
        if len(val) == 32:
            try:
                import base64
                val = base64.b32decode(val.upper()).hex().lower()
            except Exception:
                pass
        return val
    return ""


def _offline_key(url: str) -> str:
    """云下载 URL 归一化，用于和 115 任务列表里的 url / info_hash 匹配。

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
    btih = _extract_btih(u)
    if btih:
        return "btih:" + btih
    return u.rstrip("/").lower()


async def offline_check_loop():
    """核对"已提交"云下载任务的真实结果（下载完成/失败），并推送第②条通知。

    动态调度：存在已提交任务时每 30 秒轮询一次，空闲时每 60 秒轮询一次。
    双重确认机制：
    1. 115 云下载任务列表跟踪（支持 info_hash / btih / ed2k / url / 标题软化归一化）；
    2. 目标目录落盘反查兜底（即使 115 任务列表已被清空或删除，只要目标目录已有对应文件，立即确认完成）。
    """
    logstream.info("transfer", "云下载结果跟踪任务启动（动态轮询核对）")
    while True:
        try:
            subs = [dict(r) for r in await asyncio.to_thread(
                db.query, "SELECT * FROM transfers WHERE status='submitted'")]
            if not subs:
                await asyncio.sleep(60)
                continue

            # 整理独占移动阶段暂停云下载核对，防止与自动移动并发打 115（draining 排空阶段正常放行）
            await transfer_gate.wait_transfer_worker_allowed()

            try:
                tasks = await pan.run_pan_thread(pan.task_list_all)
            except Exception as e:
                logstream.warn("transfer", f"拉取 115 云下载任务列表失败，本轮跳过核对: {e}")
                await asyncio.sleep(30)
                continue

            by_key: dict[str, dict] = {}
            by_name: dict[str, dict] = {}
            for t in tasks:
                raw_url = t.get("url") or ""
                if raw_url:
                    k = _offline_key(raw_url)
                    if k:
                        by_key[k] = t
                ih = str(t.get("info_hash") or "").strip().lower()
                if ih:
                    by_key["btih:" + ih] = t
                t_name = str(t.get("name") or "").strip()
                if t_name:
                    clean_t_name = re.sub(r"[^\w\u4e00-\u9fa5]+", "", t_name.lower())
                    if clean_t_name:
                        by_name[clean_t_name] = t

            fallback_dir_cache: dict[str, list[dict]] = {}
            fallback_scanned = False

            for job in subs:
                t = None
                job_url = job.get("share_url") or ""
                job_key = _offline_key(job_url)

                # 1. 优先按 key (btih / ed2k / url) 匹配
                if job_key:
                    t = by_key.get(job_key)

                # 1.1 尝试按提交时记录的 receive_code (115 返回的 info_hash) 匹配
                if not t and job.get("receive_code"):
                    t = by_key.get("btih:" + str(job["receive_code"]).strip().lower())

                # 2. 次选按标题或磁力 dn 文件名软化匹配
                job_title = job.get("title") or ""
                clean_title = re.sub(r"[^\w\u4e00-\u9fa5]+", "", job_title.lower())
                m_dn = re.search(r"dn=([^&]+)", unquote(job_url))
                dn_str = unquote(m_dn.group(1)).strip() if m_dn else ""
                clean_dn = re.sub(r"[^\w]+", "", dn_str.lower())

                if not t:
                    for cand_key in (clean_title, clean_dn):
                        if not cand_key or len(cand_key) < 3:
                            continue
                        if cand_key in by_name:
                            t = by_name[cand_key]
                            break
                        for kname, t_cand in by_name.items():
                            if cand_key in kname or kname in cand_key:
                                t = t_cand
                                break
                        if t:
                            break

                # 3. 目标目录落盘反查兜底（极强健壮性：无论 115 任务列表是否被清空，只要目标目录文件已落盘即判定完成）
                if not t:
                    target_dir = (job.get("target_dir") or "").strip()
                    if target_dir:
                        try:
                            if target_dir in fallback_dir_cache:
                                entries = fallback_dir_cache[target_dir]
                            else:
                                # 若此时已进入整理移动阶段，跳过本轮其余目录扫描让位给整理任务
                                if transfer_gate.get_gate_state() == "organizing":
                                    break
                                if fallback_scanned:
                                    await asyncio.sleep(0.5 * pan.pace_factor())
                                fallback_scanned = True
                                dest_cid = await pan.run_pan_thread(pan.mkdirs, target_dir)
                                if dest_cid is not None:
                                    entries = await pan.run_pan_thread(pan.list_entries, dest_cid, 500)
                                    fallback_dir_cache[target_dir] = entries
                                else:
                                    entries = []
                            if entries:
                                cn_chunks = re.findall(r"[\u4e00-\u9fa5]+", job_title)
                                cn_subj = max(cn_chunks, key=len) if cn_chunks else ""
                                found_file = None
                                for e in entries:
                                    if e.get("is_dir"):
                                        continue
                                    efname = e.get("name", "")
                                    ext = os.path.splitext(efname)[1].lower()
                                    if ext in pan._MEDIA_EXTENSIONS:
                                        clean_efname = re.sub(r"[^\w]+", "", efname.lower())
                                        if (cn_subj and len(cn_subj) >= 2 and cn_subj in efname) or \
                                           (clean_title and len(clean_title) >= 3 and clean_title in re.sub(r"[^\w\u4e00-\u9fa5]+", "", efname.lower())) or \
                                           (clean_dn and len(clean_dn) >= 3 and (clean_dn in clean_efname or clean_efname in clean_dn)):
                                            found_file = e
                                            break
                                if found_file:
                                    t = {
                                        "name": found_file.get("name", ""),
                                        "status": 2,
                                        "percentDone": 100,
                                        "file_id": found_file.get("id"),
                                        "size": int(found_file.get("size", 0) or 0)
                                    }
                        except Exception as e:
                            logstream.warn("offline", f"云下载落盘反查兜底异常（任务 {job.get('share_url', '')}）: {e}")

                if t is None:
                    # 仍未找到：超过 3 天确认为失效任务，否则保持 submitted 继续等待
                    if time.time() - job["created_at"] > OFFLINE_CONFIRM_WINDOW:
                        await asyncio.to_thread(
                            _finish_offline, job, False, "",
                            "提交后 3 天内未在 115 任务列表及目标目录中找到该任务")
                    continue

                # 提取进度与状态
                try:
                    raw_pct = t.get("percentDone") or t.get("percent_done") or t.get("percent") or 0
                    percent = float(raw_pct or 0)
                except (TypeError, ValueError):
                    percent = 0.0

                try:
                    status = int(t.get("status", 0) or 0)
                except (TypeError, ValueError):
                    status = 0

                has_file = bool(t.get("file_id"))
                task_size = int(t.get("size") or t.get("file_size") or 0)
                task_name = t.get("name") or ""

                # 完成判定：
                # 115 官方规范中：percentDone>=100 或 status==2(已完成) 或 status in (10, 11) 或 status>=13 或已生成 file_id
                if percent >= 100 or status in (2, 10, 11) or status >= 13 or has_file:
                    await asyncio.to_thread(_finish_offline, job, True, task_name, "", task_size)
                elif status == -1 or status in (3, 9):
                    err_msg = t.get("err_msg") or "115 下载任务失败（链接无效或无资源）"
                    await asyncio.to_thread(_finish_offline, job, False, "", err_msg)
                # 其余状态 (如 status 1 下载中, status 4 搜索中, status 12 进行中等) 保持 submitted 等待下一轮

            await asyncio.sleep(30)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logstream.error("transfer", f"云下载结果核对异常: {logstream.exc(e)}")
            await asyncio.sleep(30)


def _finish_offline(job, ok: bool, name: str = "", reason: str = "", size: int = 0):
    job = dict(job)
    if ok:
        shown = classifier.headline(job.get("title", "")) or name or job.get("title", "")
        changed = db.execute("UPDATE transfers SET status='success', title=?, finished_at=?"
                             " WHERE id=? AND status='submitted'",
                             (shown[:200], time.time(), job["id"]))
        if changed == 0:
            return  # 已被其他核对轮次/看门狗处理，避免重复计额与重复通知
        db.quota_incr()
        logstream.info("transfer",
                       f"✅ 云下载任务完成：{shown[:60]} → {job['target_dir']}，文件已保存至目标目录")
        notify_transfer(shown, job.get("category", ""), job.get("channel", ""),
                        job["target_dir"], size=size, kind="offline_done")
    else:
        changed = db.execute("UPDATE transfers SET status='failed', error=?"
                             " WHERE id=? AND status='submitted'",
                             (reason[:500], job["id"]))
        if changed == 0:
            return
        logstream.error("transfer", f"❌ 云下载任务失败: {job['title'][:60]} — {reason}")
        notify_failure("offline", job["title"], job.get("category", ""),
                       job.get("channel", ""), reason,
                       retry_info="💡 该任务已提交 115 云下载但未成功，请检查链接有效性后重新添加")


# ---------- 每日摘要 ----------
def _bj_now():
    """摘要调度统一用北京时间：不依赖容器/宿主机时区配置（配置错也不影响 21 点推送）。"""
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # 系统缺 zoneinfo 数据时按 UTC+8 手动换算
        return datetime.now(timezone(timedelta(hours=8)))


def digest_text() -> str:
    """汇总当天的运行情况（成功/失败/队列/空间/追剧进度）。"""
    day_start = _bj_now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = db.query("SELECT status, count(*) c FROM transfers WHERE created_at>=? GROUP BY status",
                    (day_start,))
    by = {r["status"]: r["c"] for r in rows}
    queued = db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
    lines = ["📊 每日摘要", "",
             f"✅ 今日成功：{by.get('success', 0)}",
             f"❌ 失败/拦截：{by.get('failed', 0) + by.get('blocked', 0)}",
             f"⏭ 跳过/重复：{by.get('skipped', 0) + by.get('duplicate', 0)}",
             f"📥 队列剩余：{queued}"]
    if db.get_setting("cookie_ok", None):
        try:
            sp = pan.space_info()
            gb = lambda b: f"{b / (1 << 30):.1f}GB"
            lines.append(f"💾 115 空间：已用 {gb(sp['use'])}/{gb(sp['total'])}（{sp['ratio']}%）")
        except Exception:
            pass
    subs = db.query("SELECT keyword, last_ep, last_title FROM subscriptions"
                    " WHERE enabled=1 AND last_match_at IS NOT NULL ORDER BY last_match_at DESC LIMIT 5")
    if subs:
        lines.append("")
        lines.append("🎬 追剧进度：")
        for s in subs:
            ep = f"（{s['last_ep']}）" if s["last_ep"] else ""
            lines.append(f"  · {s['keyword']}{ep}")
    return "\n".join(lines)


async def digest_loop():
    """每日摘要：每天在设定时间推送一次运行汇总（默认 21:00，可关）。

    只有至少一个渠道发送成功才标记 digest_last_sent，失败时下轮（10 分钟后）
    自动重试，避免一次网络抖动导致当天摘要永久丢失。
    时间判定固定按北京时间（Asia/Shanghai），与容器/宿主机时区无关。
    """
    while True:
        try:
            if await asyncio.to_thread(db.get_setting, "digest_enabled", True):
                now = _bj_now()
                today = now.strftime("%Y-%m-%d")
                target = str(await asyncio.to_thread(db.get_setting, "digest_time", "21:00") or "21:00")
                now_hm = now.strftime("%H:%M")
                last = str(await asyncio.to_thread(db.get_setting, "digest_last_sent", "") or "")
                if last != today and now_hm >= target:
                    # digest_text 内部包含同步 pan.space_info() 网络调用：放到专用线程池防止阻塞事件循环或挤占默认池
                    text = await pan.run_pan_thread(digest_text)
                    detail = await pan.run_pan_thread(notify_detail, text)
                    if any(detail.values()):
                        await asyncio.to_thread(db.set_setting, "digest_last_sent", today)
                        sent = "、".join(k for k, v in detail.items() if v)
                        logstream.info("system", f"已推送每日摘要通知（渠道：{sent}）")
                    else:
                        # 全部渠道失败：记 warn 便于排查（未配置 Token/网络不通等），
                        # 不标记已发送，10 分钟后自动重试
                        stat = "、".join(f"{k}:{'成功' if v else '失败/未配置'}"
                                         for k, v in detail.items())
                        logstream.warn("system",
                                       f"每日摘要推送失败（{stat}）。"
                                       "请检查「通知」区渠道配置，或在设置页点「立即推送一次」验证"
                                       "（10 分钟后自动重试）")
        except Exception as e:
            logstream.error("system", f"每日摘要推送失败: {logstream.exc(e)}")
        await asyncio.sleep(600)


# ---------- 定时自动备份 ----------
def _backup_json() -> bytes:
    import json
    return json.dumps(db.backup_payload(), ensure_ascii=False, indent=1).encode()


async def auto_backup_loop():
    """定时把数据备份（JSON/Gzip）推送到用户的 TG Bot。
    按用户设定的间隔天数与具体推送时间点（如每 7 天 04:00）精准执行，并通过 TG 队列平滑推送。
    """
    while True:
        try:
            def _load_backup_meta():
                return (
                    bool(db.get_setting("auto_backup_enabled", False)),
                    int(db.get_setting("auto_backup_days", 7) or 7),
                    str(db.get_setting("notify_token", "") or "").strip(),
                    str(db.get_setting("notify_chat_id", "") or "").strip(),
                    str(db.get_setting("auto_backup_time", "04:00") or "04:00").strip(),
                    str(db.get_setting("backup_last_sent_date", "") or "").strip(),
                    float(db.get_setting("backup_last_sent", 0) or 0),
                )

            enabled, days, token, chat_id, target_time, last_sent_date, last_ts = await asyncio.to_thread(
                _load_backup_meta)
            if enabled and days > 0:
                now = _bj_now()
                today_str = now.strftime("%Y-%m-%d")
                now_hm = now.strftime("%H:%M")

                if not last_sent_date and last_ts > 0:
                    # 与 _bj_now() 统一为北京时区：本地时区 fromtimestamp 在跨时区部署会错算天数
                    last_sent_date = datetime.datetime.fromtimestamp(
                        last_ts, tz=_bj_now().tzinfo).strftime("%Y-%m-%d")

                should_send = False
                if not last_sent_date:
                    if now_hm >= target_time:
                        should_send = True
                else:
                    try:
                        last_d = datetime.datetime.strptime(last_sent_date, "%Y-%m-%d").date()
                        diff_days = (now.date() - last_d).days
                        if diff_days >= days and now_hm >= target_time and last_sent_date != today_str:
                            should_send = True
                    except Exception:
                        should_send = True

                if should_send and token and chat_id:
                    raw = await asyncio.to_thread(_backup_json)
                    # 体积超过 8MB 时自动使用 gzip 压缩，彻底规避 TG Bot 20MB 下载/50MB 发送限制
                    if len(raw) > 8 * 1024 * 1024:
                        import gzip
                        content = gzip.compress(raw, compresslevel=9)
                        fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json.gz"
                        mime = "application/gzip"
                        caption = f"🗄 定时自动备份（Gzip 压缩，原始 {len(raw) // 1024}KB → 压缩后 {len(content) // 1024}KB）"
                    else:
                        content = raw
                        fname = f"autosave_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
                        mime = "application/json"
                        caption = f"🗄 定时自动备份（核心设置/业务规则/转存与整理台账，{len(content) // 1024}KB）"

                    # 通过 TG 推送队列排队发送
                    ok = enqueue_tg_document(fname, content, mime, caption)
                    if ok:
                        def _mark_backup_sent():
                            db.set_setting("backup_last_sent", time.time())
                            db.set_setting("backup_last_sent_date", today_str)

                        await asyncio.to_thread(_mark_backup_sent)
                        logstream.info("system", f"定时自动备份已加入 TG 发送队列（{fname}，{len(content) // 1024}KB）")
                    else:
                        logstream.warn("system", "定时自动备份入队失败（队列已满）")
        except Exception as e:
            logstream.error("system", f"自动备份异常: {logstream.exc(e)}")
        await asyncio.sleep(60)


# ---------- 定时文件整理（按目录映射移动中文文件）----------
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


async def execute_auto_move(trigger: str = "scheduled") -> dict:
    """按 auto_move_rules 顺序排队执行文件移动整理（做好 115 严格防风控与通知推送）。"""
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
            risk_aborted = False
            scan_cache: dict = {}

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

                try:
                    moved_items: list = []
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
                        "keyword": kw, "mode": effective_mode, "moved": moved_count, "sample_names": sample_names
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
                            notify,
                            f"⚠️ 【115 风控拦截】自动整理文件时检测到 115 限制信号（{err_str[:100]}），已紧急熔断停止后续移动操作，请检查账号状态！"
                        )
                        break

                # 规则与规则之间的排队间隔，自适应风控防封号
                pace = pan.pace_factor()
                await asyncio.sleep(1.5 * pace)

            total_moved = sum(d.get("moved", 0) for d in details)
            await asyncio.to_thread(db.set_setting, "auto_move_last_run", time.time())

            # 移动完成推送通知
            if await asyncio.to_thread(db.get_setting, "auto_move_notify", True) and (
                    total_moved > 0 or trigger == "manual" or risk_aborted):
                trig_desc = "手动排队执行" if trigger == "manual" else "定时计划执行"
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                lines = [
                    "📁 115 文件整理任务完成",
                    f"• 触发方式：{trig_desc}",
                    f"• 完成时间：{now_str}",
                    f"• 规则统计：共处理 {len(rules)} 条规则，成功移动 {total_moved} 个文件",
                ]
                if risk_aborted:
                    lines.append("⚠️ 注意：任务因触发 115 接口风控而提前中断，部分规则未执行")
                lines.append("")
                lines.append("明细：")
                for d in details:
                    if d.get("error"):
                        lines.append(f"• {d['target_dir']}：❌ 执行失败（{d['error'][:50]}）")
                    elif d.get("moved", 0) > 0:
                        samples = "、".join(d.get("sample_names", [])[:2])
                        extra = f"（如 {samples} 等）" if samples else ""
                        lines.append(f"• {d['target_dir']}（关键词: {d['keyword']}）：移动 {d['moved']} 个文件{extra}")
                    else:
                        lines.append(f"• {d['target_dir']}（关键词: {d['keyword']}）：无新文件")
                lines.append("")
                lines.append("💡 提示：文件夹均保持原地，仅移动含中文且含目标名称的正片文件。")
                full_msg = chr(10).join(lines)
                await pan.run_pan_thread(notify, full_msg)

            res = {"ok": True, "total_rules": len(rules), "total_moved": total_moved, "details": details}
            _last_auto_move_result = res
            return res
        finally:
            # 整理完成或异常中止，停止移动任务并恢复新任务接收与转存工作器
            transfer_gate.resume_all()


async def preview_auto_move(rule_ids: list[int] | None = None) -> dict:
    """整理前预览（dry_run）：按启用规则扫描源目录，返回「将会移动哪些文件」的计划。

    只读不写：不做任何移动/重命名；不进独占门闸（不需要暂停转存工作器），
    但独占任务执行中拒绝预览（避免叠加 115 扫描请求），并与正式执行共用模块锁互斥。
    预览的 115 请求量与正式执行相当（只少 fs_move），结果为当前时刻快照，
    确认执行时会重新扫描（期间新增/手动移走的文件与预览存在差异）。
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
        out_rules: list[dict] = []
        total_planned = 0
        risk_aborted = False

        for r in rules:
            s_dir = r.get("source_dir", "").strip()
            t_dir = r.get("target_dir", "").strip()
            rule_mode = r.get("mode") or "default"
            effective_mode = rule_mode if rule_mode in ("library", "exact") else global_mode
            kw = (r.get("keyword", "") or pan.extract_target_folder_name(t_dir)).strip()
            entry = {"rule_id": r["id"], "source_dir": s_dir, "target_dir": t_dir,
                     "mode": effective_mode, "keyword": kw,
                     "planned": [], "skip_ep": 0, "skip_ep_samples": [], "error": ""}

            if effective_mode == "exact" and not kw:
                entry["error"] = "目标文件夹名为空，无法提取匹配关键词"
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
                )
                if isinstance(plan, dict):
                    entry["planned"] = plan.get("items", [])
                    entry["skip_ep"] = int(plan.get("skip_ep") or 0)
                    entry["skip_ep_samples"] = list(plan.get("skip_ep_samples") or [])
                total_planned += len(entry["planned"])
                logstream.info("auto_move",
                               f"预览 [{s_dir} ➔ {t_dir}]：计划移动 {len(entry['planned'])} 个文件"
                               + (f"，按季集防重将跳过 {entry['skip_ep']} 个" if entry["skip_ep"] else ""))
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
        return {"ok": True, "total_planned": total_planned, "rules": out_rules,
                "risk_aborted": risk_aborted}


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

