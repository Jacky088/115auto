"""TG Bot 指令分发、菜单交互与 Long Polling 主循环。"""
import asyncio
import time

import httpx

from ... import database as db
from ...core import logstream
from .. import jobs
from .backup_flow import (
    _do_restore,
    _restore_allowed,
    _restore_guide,
    restore_upload_flow,
    send_backup_flow,
)
from .link_flow import (
    _bot_enqueue_links,
    _parse_link_header,
    _submode_text,
    _usage_add,
)
from .scrape_flow import _handle_scid_command, _handle_scrape_callback
from . import state
from .state import BOT_MENU_COMMANDS, HELP, _tg_proxy
from .sub_flow import (
    _backfill_candidates,
    _backfill_confirm,
    _backfill_execute,
    _handle_sub,
    _handle_subpick,
    add_channel_flow,
    sub_search_flow,
)


def _tb(b: float) -> str:
    """115 容量统一以 TB 显示（网盘容量均为 TB 级，GB 数字过长不易读）。"""
    return f"{b / (1 << 40):.1f}TB"


def _check_sender(msg: dict) -> tuple[bool, str]:
    """发送者鉴权：chat_id 匹配之外，还要求发送者是绑定的管理员用户。"""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    chat_type = str(msg.get("chat", {}).get("type") or "")
    sender = str((msg.get("from") or {}).get("id") or "")
    if not sender:
        logstream.warn("system", f"TG Bot 收到对话 {chat_id} 中无发送者信息的消息，已拒绝执行指令")
        return False, "无法识别发送者（频道自动转发等消息不支持 Bot 指令）"
    stored = str(db.get_setting("tg_admin_id", "") or "")
    stored_uid = stored.split(":")[-1] if stored else ""
    if not stored_uid:
        if chat_type != "private":
            logstream.warn("system", f"TG Bot 收到群聊 {chat_id} 中用户 {sender} 的首次指令，"
                                     "已拒绝（管理员绑定仅限私聊完成）")
            return False, ("该 Bot 尚未绑定管理员。出于安全考虑，请在与 Bot 的私聊中发送任意指令"
                           "完成绑定，之后即可在群内使用。")
        db.set_setting("tg_admin_id", f"{chat_id}:{sender}")
        logstream.warn("system", f"TG Bot 已绑定管理员用户 {sender}（私聊自动绑定；"
                                 "如非本人操作，请立即到网页改绑或停止使用该 Bot）")
        return True, ""
    if stored_uid != sender:
        logstream.warn("system", f"TG Bot 收到非管理员用户 {sender} 的指令，已拒绝")
        return False, "仅绑定的管理员用户可使用 Bot 指令"
    return True, ""


async def register_bot_menu(client: httpx.AsyncClient, token: str) -> None:
    """把指令清单注册到 Telegram（setMyCommands），失败不影响运行。"""
    try:
        r = await client.post(f"https://api.telegram.org/bot{token}/setMyCommands",
                              json={"commands": BOT_MENU_COMMANDS})
        if r.status_code == 200 and r.json().get("ok"):
            logstream.info("system", "TG Bot 指令菜单已注册（输入框左侧菜单按钮，或输入 / 时可见）")
        else:
            logstream.warn("system", f"TG Bot 菜单注册失败: {str(r.text)[:120]}")
    except Exception as e:
        logstream.warn("system", f"TG Bot 菜单注册异常: {logstream.exc(e)}")


def _archive_status() -> str:
    """/archive：定时自动归档分流状态一览。"""
    from ...core import transfer_gate
    on = bool(db.get_setting("auto_move_enabled", False))
    gate = transfer_gate.get_exclusive_status()
    gate_line = "空闲" if not gate.get("busy") else f"{gate.get('mode_label', '任务')}执行中"
    lines = ["📦 定时自动归档分流", "",
             f"• 功能：{'已开启' if on else '未开启'}｜门闸：{gate_line}"]
    if on:
        lr = db.query("SELECT source_dir, last_run_at, last_moved_count FROM auto_move_rules"
                      " WHERE last_run_at IS NOT NULL ORDER BY last_run_at DESC LIMIT 1", one=True)
        if lr:
            t = time.strftime("%m-%d %H:%M", time.localtime(lr["last_run_at"]))
            lines.append(f"• 上次执行：{t} · 移动 {lr['last_moved_count']} 个文件（{lr['source_dir']}）")
        else:
            lines.append("• 上次执行：尚未执行")
        nxt = jobs.compute_next_runs()
        if nxt:
            lines.append(f"• 下次触发：{nxt[0]}")
        in_win = transfer_gate.is_in_schedule_window()
        if not in_win:
            lines.append("• 当前处于防风控静默时段外，触发将自动顺延")
    lines.append("")
    lines.append("发送 /archive run 立即排队归档（按钮确认）")
    return "\n".join(lines)


def _system_start() -> str:
    """/start：启动系统（复用网页的就绪校验与启动逻辑，行为完全一致）。"""
    from ...api.manage import system_start
    try:
        system_start()
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e)
        return f"无法启动：{detail}"
    logstream.info("system", "▶ 已通过 TG 指令启动系统（/start）")
    return "▶ 系统已启动（监控与转存运行中）"


def _system_stop() -> str:
    """/stop：停止系统（与网页停止完全一致：归档功能联动关闭）。"""
    from ...api.manage import system_stop
    system_stop()
    logstream.warn("system", "⏹ 已通过 TG 指令停止系统（/stop）")
    return "⏹ 系统已停止（归档功能已联动关闭）。发送 /start 可重新启动"


def _menu_keyboard() -> dict:
    """快捷操作菜单：按钮随当前状态变化（暂停/恢复）。订阅模式按频道独立设置。"""
    return {"inline_keyboard": [
        [{"text": "📊 运行状态", "callback_data": "c:status"},
         {"text": "🕘 最近转存", "callback_data": "c:ls"}],
        [{"text": "🔄 重试失败任务", "callback_data": "c:retry"}],
        [{"text": "▶ 启动系统" if str(db.get_setting("running", False)).lower() != "true"
                  else "⏹ 停止系统", "callback_data": "c:toggle_running"}],
        [{"text": "🎬 订阅列表", "callback_data": "c:sub"},
         {"text": "📺 频道订阅模式", "callback_data": "c:submode"}],
        [{"text": "🗄 立即导出备份", "callback_data": "c:backup"},
         {"text": "♻️ 从备份恢复", "callback_data": "c:restore"}],
        [{"text": "❓ 全部指令帮助", "callback_data": "c:help"}],
    ]}


def _handle_command(text: str, chat_id: str) -> str:
    """执行指令，返回回复文本。"""
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]
    if cmd == "/status":
        queued = db.query("SELECT count(*) c FROM transfers WHERE status='queued'", one=True)["c"]
        today_s = db.query("SELECT count(*) c FROM transfers WHERE status='success' AND created_at>?",
                           (time.time() - 86400,), one=True)["c"]
        today_f = db.query("SELECT count(*) c FROM transfers WHERE status IN ('failed','blocked')"
                           " AND created_at>?", (time.time() - 86400,), one=True)["c"]
        quota = db.quota_today()
        limit = db.get_setting("daily_limit", 50)
        paused = db.get_setting("paused", False)
        space_line = ""
        if db.get_setting("cookie_ok", None):
            try:
                from ...pan115 import client as pan
                sp = pan.space_info()
                space_line = (f"\n💾 115 空间：已用 {_tb(sp['use'])}/{_tb(sp['total'])}"
                              f"（{sp['ratio']}%）· 剩余 {_tb(sp.get('free', 0))}")
            except Exception:
                pass
        running = db.get_setting("running", False)
        state_line = ("▶ 运行中" if running and not paused
                      else "⏸ 已暂停" if paused else "⏹ 未启动（发送 /start 或到网页顶栏「开始运行」启动）")
        return (f"📊 运行状态\n\n{state_line}\n"
                f"📥 队列：{queued} 个待转存\n"
                f"✅ 近 24 小时成功：{today_s}｜❌ 失败/拦截：{today_f}\n"
                f"📈 每日限额：{quota}/{limit}{space_line}")
    if cmd == "/add":
        return _usage_add()
    if cmd == "/ls":
        rows = db.query("SELECT title, status, kind, increment, target_dir, created_at FROM transfers"
                        " ORDER BY id DESC LIMIT 5")
        if not rows:
            return "暂无转存记录"
        label = {"success": "✅", "failed": "❌", "blocked": "⛔", "queued": "⏳",
                 "running": "🔄", "skipped": "⏭", "duplicate": "🔁", "submitted": "☁️"}
        lines = ["🕘 最近转存", ""]
        for r in rows:
            title = (r["title"] or "-")[:40]
            mark = label.get(r["status"], "·")
            kind_mark = ("☁️" if r["kind"] == "offline"
                         else "🔁" if r["increment"] else "")
            lines.append(f"{mark} {kind_mark}{title}")
        return "\n".join(lines)
    if cmd == "/quota":
        return "Bot 暂不支持修改每日限额，请在网页「系统设置」中调整（默认 50）"
    if cmd == "/sub":
        return _handle_sub(args)
    if cmd == "/archive":
        return _archive_status()
    if cmd == "/start":
        return _system_start()
    if cmd == "/stop":
        return "⚠️ 将停止系统运行：暂停监听与转存，归档功能联动关闭。确认请点按钮，或直接忽略"
    if cmd == "/retry":
        n = db.execute(
            "UPDATE transfers SET status='queued', attempts=0, error='', next_retry_at=NULL"
            " WHERE status='failed'"
            " AND id=(SELECT MAX(id) FROM transfers t2"
            "         WHERE t2.share_code=transfers.share_code AND t2.status='failed')"
            " AND share_code NOT IN (SELECT share_code FROM transfers"
            "                         WHERE status IN ('queued','running','submitted'))")
        logstream.info("transfer", f"已通过 TG 指令重试 {n} 个失败任务（/retry）")
        return f"🔄 已重试 {n} 个失败任务" if n else "当前没有失败任务"
    if cmd == "/help":
        return HELP
    if cmd == "/menu":
        return "发送 /menu 打开快捷操作菜单"
    return f"未知指令 {cmd}\n\n{HELP}"


def _handle_callback(data: str) -> tuple[str, dict]:
    """处理菜单按钮点击，返回 (回复文本, 键盘)。动作执行后菜单会原位刷新。"""
    data = (data or "").strip()
    if data == "c:toggle_running":
        running = str(db.get_setting("running", False)).lower() == "true"
        if not running:
            return _system_start(), _menu_keyboard()
        return "⚠️ 确认停止系统？（监听与转存暂停，归档功能联动关闭）", {
            "inline_keyboard": [
                [{"text": "⏹ 确认停止", "callback_data": "s:stopgo"}],
                [{"text": "❌ 取消", "callback_data": "c:status"}],
            ]
        }
    if data == "s:stopgo":
        return _system_stop(), _menu_keyboard()
    if data == "a:cancel":
        return "已取消归档", {}
    if data == "c:submode":
        return _submode_text(), _menu_keyboard()
    if data == "c:restore":
        return _restore_guide(), _menu_keyboard()
    if data.startswith("c:"):
        cmd = data[2:].split()[0] if data[2:].strip() else ""
        args = data[2:].split()[1:]
        if cmd in ("status", "ls", "sub", "retry", "help"):
            text = _handle_command(f"/{cmd} {' '.join(args)}".strip(), "")
            return text, (_menu_keyboard() if cmd != "help" else {})
    return "未知操作", _menu_keyboard()


async def bot_command_loop():
    """TG Bot long polling：处理远程指令。未配置 Token 时跳过。

    每轮 long polling 前重新读取 Token/Chat ID/代理：网页改了配置后，
    当前客户端会被主动关闭并按新配置重建（配置热更新）。

    可靠性：
    - TG long polling 服务端最长挂起 timeout 秒才返回，读超时（ReadTimeout）是
      正常现象（代理抖动/网络慢时尤其常见）：静默重试即可，不当异常刷屏，
      也不会重建客户端（连接复用，避免每 40 秒重启一轮）。
    - 「已启动」只在配置变化后的第一次连接时提示一次，异常恢复重连不重复刷屏。
    - 指令处理（含查 115 空间等同步网络调用）放线程池执行，不阻塞事件循环。
    """
    announced = None  # 已提示「已启动」的配置指纹 (token, chat_id, proxy)
    def _bot_cfg():
        return (
            str(db.get_setting("notify_token", "") or "").strip(),
            str(db.get_setting("notify_chat_id", "") or "").strip(),
            _tg_proxy(),
        )

    while True:
        try:
            token, chat_id, proxy = await asyncio.to_thread(_bot_cfg)
        except Exception as e:
            # DB 瞬时异常不能杀死主循环（否则 TG 远程指令静默失效且健康监控无感知）
            logstream.error("system", f"TG Bot 配置读取异常，30 秒后重试: {logstream.exc(e)}")
            await asyncio.sleep(30)
            continue
        if not token or not chat_id:
            announced = None
            await asyncio.sleep(30)
            continue
        try:
            # 读超时必须明显大于 TG 服务端挂起时长，正常 long polling 轮次不会触顶；
            # 剩余的偶发超时由下方 TimeoutException 分支静默处理
            timeout = httpx.Timeout(45.0, connect=15.0)
            async with httpx.AsyncClient(proxy=proxy, timeout=timeout) as client:
                fingerprint = (token, chat_id, proxy)
                if announced != fingerprint:
                    await register_bot_menu(client, token)  # 顺手把指令菜单注册到 TG
                    logstream.info("system", "TG Bot 远程指令已启动（/help 查看指令）")
                    announced = fingerprint
                while True:
                    # 配置变化 → 退出内层循环，用新配置重建客户端
                    cur_token, cur_chat, cur_proxy = await asyncio.to_thread(_bot_cfg)
                    if cur_token != token or cur_chat != chat_id or cur_proxy != proxy:
                        break
                    params = {"timeout": 25, "offset": state._last_update_id + 1,
                              "allowed_updates": '["message","callback_query"]'}
                    try:
                        resp = await client.get(
                            f"https://api.telegram.org/bot{token}/getUpdates", params=params)
                    except httpx.TimeoutException:
                        continue  # long polling 超时/网络抖动：正常现象，立即发起下一轮
                    if resp.status_code != 200:
                        await asyncio.sleep(10)
                        continue
                    try:
                        updates = resp.json().get("result", [])
                    except Exception:
                        await asyncio.sleep(5)
                        continue
                    for upd in updates:
                        state._last_update_id = max(state._last_update_id, upd["update_id"])

                        # ---- 菜单按钮点击（callback_query）：原位编辑菜单消息 ----
                        cq = upd.get("callback_query")
                        if cq:
                            if str(cq.get("message", {}).get("chat", {}).get("id", "")) != chat_id:
                                continue
                            sender_ok, sender_hint = await asyncio.to_thread(
                                _check_sender,
                                {"chat": cq.get("message", {}).get("chat", {}),
                                 "from": cq.get("from") or {}})
                            if not sender_ok:
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": sender_hint})
                                except Exception:
                                    pass
                                continue
                            cb_raw = str(cq.get("data") or "")
                            if cb_raw.startswith("sc"):
                                await _handle_scrape_callback(client, token, chat_id, cq)
                                continue
                            try:  # 先应答回调，去掉按钮上的加载动画
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                                    json={"callback_query_id": cq.get("id")})
                            except Exception:
                                pass
                            # 订阅搜索点选：独立流程，回复新消息
                            if (cq.get("data") or "").startswith("subpick:"):
                                await _handle_subpick(client, token, chat_id, cq["data"])
                                continue
                            # 向前补全：候选点选 / 确认执行 / 取消
                            if (cq.get("data") or "").startswith("bfpick:"):
                                parts = (cq.get("data") or "").split(":")
                                try:
                                    tid = int(parts[1])
                                    ep_hint = int(parts[2]) if len(parts) > 2 else 0
                                except ValueError:
                                    tid = 0
                                reply, kb = await asyncio.to_thread(
                                    _backfill_confirm, tid, ep_hint)
                                payload = {"chat_id": chat_id, "text": reply}
                                if kb:
                                    payload["reply_markup"] = kb
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json=payload)
                                except Exception:
                                    pass
                                continue
                            if (cq.get("data") or "") == "c:backup":
                                try:
                                    await send_backup_flow(client, token, chat_id)
                                except Exception as e:
                                    logstream.error("system", f"Bot 发送备份流程异常: {logstream.exc(e)}")
                                continue
                            if (cq.get("data") or "").startswith("bfgo:"):
                                parts = (cq.get("data") or "").split(":")
                                try:
                                    tid = int(parts[1])
                                    ep_hint = int(parts[2]) if len(parts) > 2 else 0
                                except ValueError:
                                    tid, ep_hint = 0, 0
                                reply = await _backfill_execute(tid, ep_hint)
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": reply})
                                except Exception:
                                    pass
                                continue
                            if (cq.get("data") or "").startswith("bfcancel:"):
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": "已取消"})
                                except Exception:
                                    pass
                                continue
                            # 归档确认：a:run 异步执行（可能耗时数分钟），a:cancel 原位改回执
                            if (cq.get("data") or "") == "a:cancel":
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/editMessageText",
                                        json={"chat_id": chat_id,
                                              "message_id": cq.get("message", {}).get("message_id"),
                                              "text": "已取消归档"})
                                except Exception:
                                    pass
                                continue
                            if (cq.get("data") or "") == "a:run":
                                async def _bg_auto_move(msg_id):
                                    try:
                                        res = await jobs.execute_auto_move(trigger="manual")
                                        reply = "✅ 归档执行完成（详情见推送通知）" if res.get("ok") else (res.get("msg") or "归档发起失败")
                                    except Exception as e:
                                        reply = f"❌ 归档执行失败: {str(e)[:150]}"
                                        logstream.error("auto_move", f"Bot 触发归档失败: {logstream.exc(e)}")
                                    try:
                                        async with httpx.AsyncClient(proxy=_tg_proxy(), timeout=15.0) as c2:
                                            await c2.post(
                                                f"https://api.telegram.org/bot{token}/editMessageText",
                                                json={"chat_id": chat_id, "message_id": msg_id, "text": reply})
                                    except Exception:
                                        pass

                                asyncio.create_task(_bg_auto_move(cq.get("message", {}).get("message_id")))
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/editMessageText",
                                        json={"chat_id": chat_id,
                                              "message_id": cq.get("message", {}).get("message_id"),
                                              "text": "⏳ 已接收归档指令，正在后台排空队列并执行移动，完成后将更新结果…"})
                                except Exception:
                                    pass
                                continue
                            try:
                                reply, kb = await asyncio.to_thread(
                                    _handle_callback, cq.get("data") or "")
                            except Exception as e:
                                reply, kb = f"操作失败: {e}", {}
                            try:  # 原位编辑菜单消息（结果 + 刷新后的按钮）
                                payload = {"chat_id": chat_id,
                                           "message_id": cq.get("message", {}).get("message_id"),
                                           "text": reply}
                                if kb:
                                    payload["reply_markup"] = kb
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/editMessageText",
                                    json=payload)
                            except Exception:
                                try:  # 消息未变化等编辑失败场景：退化为发新消息
                                    payload = {"chat_id": chat_id, "text": reply}
                                    if kb:
                                        payload["reply_markup"] = kb
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json=payload)
                                except Exception:
                                    pass
                            continue

                        # ---- 文本指令 ----
                        msg = (upd.get("message")
                               or upd.get("channel_post")
                               or upd.get("edited_message") or {})
                        msg_chat_id = str(msg.get("chat", {}).get("id", ""))
                        if msg_chat_id != chat_id:
                            # notify 配置为群聊时，允许来自任意私聊的首次指令完成管理员绑定（TOFU）；
                            # 绑定完成后指令仍仅在授权对话内执行
                            if (str(msg.get("chat", {}).get("type") or "") == "private"
                                    and not str(await asyncio.to_thread(db.get_setting, "tg_admin_id", "") or "").strip()):
                                ok, _ = await asyncio.to_thread(_check_sender, msg)
                                if ok:
                                    try:
                                        await client.post(
                                            f"https://api.telegram.org/bot{token}/sendMessage",
                                            json={"chat_id": msg_chat_id,
                                                  "text": "✅ 管理员绑定成功，现在可在已配置的通知群聊/对话中使用 Bot 指令。"})
                                    except Exception:
                                        pass
                            continue  # 非授权对话
                        sender_ok, sender_hint = await asyncio.to_thread(_check_sender, msg)
                        if not sender_ok:
                            try:
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/sendMessage",
                                    json={"chat_id": chat_id, "text": sender_hint})
                            except Exception:
                                pass
                            continue  # 非绑定的管理员用户（群聊防成员滥用）
                        # ---- 备份文件恢复：直接发送/转发备份 JSON/GZ/ZIP 给 Bot ----
                        doc = msg.get("document")
                        if doc and (str(doc.get("mime_type") or "").endswith(("json", "gzip", "zip", "octet-stream"))
                                    or str(doc.get("file_name") or "").lower().endswith((".json", ".json.gz", ".gz", ".zip"))):
                            allowed, hint = _restore_allowed(msg)
                            if not allowed:
                                reply = hint
                            else:
                                try:
                                    reply = await restore_upload_flow(client, token, chat_id, doc)
                                except Exception as e:
                                    reply = f"备份文件处理失败: {e}"
                            try:
                                await client.post(
                                    f"https://api.telegram.org/bot{token}/sendMessage",
                                    json={"chat_id": chat_id, "text": reply})
                            except Exception:
                                pass
                            continue
                        text = (msg.get("text") or "").strip()
                        if not text.startswith("/"):
                            # 管理员直接发送链接：必须带「转存 / 云下载」指令头。
                            # 缺指令头但含链接 → 回复格式说明；无链接且无指令头 → 静默
                            from ...core import extractor as _ext
                            if _ext.extract(text) or _parse_link_header(text)[0]:
                                try:
                                    reply = await asyncio.to_thread(
                                        _bot_enqueue_links, text, msg.get("message_id") or 0)
                                except Exception as e:
                                    reply = f"入队失败: {e}"
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": reply})
                                except Exception:
                                    pass
                            continue
                        try:
                            if text.lower().startswith("/backup"):
                                await send_backup_flow(client, token, chat_id)
                                continue
                            elif text.lower().startswith("/restore"):
                                allowed, hint = _restore_allowed(msg)
                                arg = text.split(None, 1)[1].strip().lower() \
                                    if len(text.split(None, 1)) > 1 else ""
                                if not allowed:
                                    reply = hint  # 群聊里禁止恢复（含 /restore confirm）
                                elif arg == "confirm":
                                    reply = await asyncio.to_thread(_do_restore, chat_id)
                                else:
                                    reply = _restore_guide()
                            elif text.lower().startswith("/menu"):
                                reply = "🛠 快捷操作（点按钮执行，执行后菜单原位刷新）"
                                try:
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json={"chat_id": chat_id, "text": reply,
                                              "reply_markup": _menu_keyboard()})
                                except Exception:
                                    pass
                                continue
                            elif text.lower().startswith("/add"):
                                # /add 需要异步做频道可访问性校验，单独处理
                                reply = await add_channel_flow(text.split(None, 1)[1]) \
                                    if len(text.split(None, 1)) > 1 \
                                    else await asyncio.to_thread(_usage_add)
                            elif text.lower().startswith("/archive"):
                                parts_a = text.split()
                                if len(parts_a) > 1 and parts_a[1].lower() == "run":
                                    reply = ("将立即排队执行归档：等待转存/云下载队列排空后自动移动。"
                                             "确认请点按钮")
                                    kb = {"inline_keyboard": [
                                        [{"text": "✅ 确认归档", "callback_data": "a:run"}],
                                        [{"text": "❌ 取消", "callback_data": "a:cancel"}]]}
                                    try:
                                        await client.post(
                                            f"https://api.telegram.org/bot{token}/sendMessage",
                                            json={"chat_id": chat_id, "text": reply,
                                                  "reply_markup": kb})
                                    except Exception:
                                        pass
                                    continue
                                reply = await asyncio.to_thread(_handle_command, "/archive", chat_id)
                            elif text.lower().startswith("/backfill"):
                                arg = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
                                reply, kb = await asyncio.to_thread(_backfill_candidates, arg)
                                try:
                                    payload = {"chat_id": chat_id, "text": reply}
                                    if kb:
                                        payload["reply_markup"] = kb
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/sendMessage",
                                        json=payload)
                                except Exception:
                                    pass
                                continue
                            elif text.lower().startswith("/scid"):
                                reply = await asyncio.to_thread(_handle_scid_command, text)
                            elif text.lower().startswith("/sub"):
                                # /sub <片名>（非 del）：搜索订阅流程（异步 + 海报上传）
                                parts = text.split()
                                if len(parts) > 1 and parts[1].lower() != "del":
                                    await sub_search_flow(client, token, chat_id,
                                                          " ".join(parts[1:])[:100])
                                    continue
                                reply = await asyncio.to_thread(_handle_command, text, chat_id)
                            else:
                                reply = await asyncio.to_thread(_handle_command, text, chat_id)
                        except Exception as e:
                            reply = f"指令执行失败: {e}"
                        try:
                            await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                              json={"chat_id": chat_id, "text": reply})
                        except Exception:
                            pass  # 回复发送失败不影响指令已执行，下轮继续
        except Exception as e:
            announced = None  # 连接层异常（代理断开等）：恢复后重新提示一次「已启动」
            logstream.warn("system", f"TG Bot 指令轮询异常: {logstream.exc(e)}")
            await asyncio.sleep(15)
        await asyncio.sleep(1)
