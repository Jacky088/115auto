"""通知推送模块：TG 异步推送队列、多渠道通知（TG/Bark/PushPlus/ntfy）与转存/空间告警。"""
import asyncio
import queue
import threading
import time

from ... import database as db
from ...core import classifier, logstream
from ...pan115 import client as pan
from .webhook import send_webhook

def _jobs():
    """延迟获取 jobs 包门面，避免循环导入并保证测试 patch 透明生效。"""
    from .. import jobs
    return jobs


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
        _jobs()._tg_notify_queue.put_nowait(item)
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
        _jobs()._tg_notify_queue.put_nowait({
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
        _jobs()._tg_notify_queue.put_nowait({
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
                item = _jobs()._tg_notify_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.5)
                continue

            item_type = item.get("type", "text")
            token = item["token"]
            chat_id = item["chat_id"]
            proxy = _jobs()._tg_proxy()

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
                        async with _jobs().httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
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
                        async with _jobs().httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
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
                        async with _jobs().httpx.AsyncClient(proxy=proxy, timeout=60.0) as client:
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

            _jobs()._tg_notify_queue.task_done()
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
    return _jobs().enqueue_tg_message(message)


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
        resp = _jobs().httpx.get(f"{base}/{quote(message[:400])}",
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
        resp = _jobs().httpx.post("https://www.pushplus.plus/send", json=payload, timeout=10)
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
        resp = _jobs().httpx.post(url, content=message.encode()[:4000],
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
    "webhook": send_webhook,
}

# 渠道未配置时的提示文案（前端测试按钮直接展示，帮助用户补配置）
CHANNEL_EMPTY_HINT = {
    "tg": "未配置 TG Bot Token / Chat ID（「通知」区）",
    "bark": "未填写 Bark 推送 URL（形如 https://api.day.app/你的key）",
    "pushplus": "未填写 PushPlus Token",
    "ntfy": "未填写 ntfy 主题 URL（形如 https://ntfy.sh/你的主题）",
    "webhook": "未填写 Webhook 推送地址（支持企业微信/飞书/钉钉/自定义 JSON）",
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
        if fn("🔔 115 自动转存助手测试通知\n通知渠道配置正常。"):
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
        resp = _jobs().httpx.get("https://api.telegram.org", proxy=proxy, timeout=12)
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
        resp = _jobs().httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id,
                  "text": "🔔 115 自动转存助手测试通知\n通知渠道配置正常。",
                  "disable_web_page_preview": True},
            proxy=_jobs()._tg_proxy(), timeout=15,
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
    from ...core import episode

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
        _jobs().notify("\n".join(lines))
    except Exception:
        pass


BIG_TRANSFER_SIZE = 1 << 40  # 1 TB：超大资源醒目标识阈值（单个或整目录合计都按此判断）


def notify_transfer(title: str, category: str, source: str, target: str,
                    size: int = 0, kind: str = "share", file_count: int = 0):
    """转存/云下载结果通知（简洁格式）。kind: share 分享转存成功 | offline 已提交云下载 | offline_done 云下载完成。失败静默，不影响主流程。"""
    try:
        if not db.get_setting("notify_transfer_success", True):
            return
        title = classifier.headline(title) or title
        head = {"offline": "☁️ 云下载任务已添加", "offline_done": "✅ 云下载任务完成"}.get(kind, "✅ 转存成功")
        big = bool(size and size >= BIG_TRANSFER_SIZE)
        if big:  # 超大资源：标题加红块前缀，一眼可见
            head = "🟥 " + head + "｜超大资源 🟥"
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
            lines.append("① 已提交 115 云下载，完成后将发第②条通知")
        elif kind == "offline_done":
            lines.append("② 文件已落盘至目标目录")
        if size:
            if big:
                lines.append("🚨 本次占用：" + _fmt_size(size) + "（≥ 1 TB，请留意 115 空间）")
            else:
                lines.append("💾 大小：" + _fmt_size(size))
        if file_count:
            lines.append("🗃 文件数：" + str(file_count))
        _jobs().notify("\n".join(lines))
    except Exception:
        pass  # 通知失败不影响转存


def check_space_alert(trigger: str = ""):
    """115 空间告急提醒：使用率跨过 90% / 95% 时推送一次醒目通知。

    阈值与仪表盘空间条变红一致（≥90%）。同一档位 24 小时内不重复推送，避免
    每笔大转存都刷屏；空间回落后自动降档，再次涨上来可重新提醒。
    失败只记日志，绝不影响转存主流程。
    """
    try:
        if not db.get_setting("notify_transfer_success", True):
            return
        sp = pan.space_info()
        total = int(sp.get("total") or 0)
        use = int(sp.get("use") or 0)
        if total <= 0:
            return
        ratio = use * 100.0 / total
        level = 95 if ratio >= 95 else 90 if ratio >= 90 else 0
        prev = int(db.get_setting("space_alert_level", 0) or 0)
        if level == 0:
            if prev:
                db.set_setting("space_alert_level", 0)
            return
        if level < prev:  # 空间略有回落（如 95% → 92%）：静默降档，便于再次跨档提醒
            db.set_setting("space_alert_level", level)
            return
        last = float(db.get_setting("space_alert_at", 0) or 0)
        if level <= prev and time.time() - last < 86400:
            return  # 同档位 24 小时内只提醒一次
        remain = max(total - use, 0)
        _jobs().notify("🟥🟥 115 空间告急！ 🟥🟥\n\n"
               f"📊 已用：{_fmt_size(use)} / {_fmt_size(total)}（{ratio:.1f}%）\n"
               f"📉 剩余：{_fmt_size(remain)}\n"
               + (f"📌 触发：{trigger}\n" if trigger else "") +
               "\n⚠️ 空间接近上限，后续转存/云下载可能失败。请到 115 网盘或网页「网盘文件」清理，也可在「系统设置」调低每日上限。\n"
               "空间降至 90% 以下后自动解除。")
        db.set_setting("space_alert_level", level)
        db.set_setting("space_alert_at", time.time())
        logstream.warn("system",
                       f"115 空间告急：已用 {ratio:.1f}%（{_fmt_size(use)} / {_fmt_size(total)}）")
    except Exception as e:
        logstream.warn("system", f"空间告急检查失败: {e}")
